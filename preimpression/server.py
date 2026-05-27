"""
preimpression/server.py
=======================
Flask blueprint exposing the pre-impression pipeline as an HTTP endpoint.

This is a refactor of az_server_v2.py for landing into the existing Flask
app. Key changes from the standalone version:

  - Module-level Flask Blueprint instead of `app = Flask(__name__)`.
  - Auth decorators @require_api_key + @dicom_profile_required replace
    the env-var check.
  - Customer profile gating via g.customer (matching the existing
    /dicom/analyze pattern).
  - Imports use the .pipeline relative path (inside the preimpression package).

Endpoints:
    POST /preimpression                            — JSON (default)
    POST /preimpression?format=markdown            — markdown
    POST /preimpression?format=text                — plain text
    POST /preimpression?format=visualization       — PNG (3D registration view)
    POST /preimpression?body_part=BRAIN            — override autodetect
    POST /preimpression?include_boundaries=true    — full boundary polygons in JSON
    GET  /supported-body-parts

Register in app.py:

    try:
        from preimpression.server import preimpression_bp
        app.register_blueprint(preimpression_bp)
        print("[app] preimpression loaded", flush=True)
    except Exception as e:
        print(f"[app] preimpression failed: {e}", flush=True)
"""
from __future__ import annotations
import os
import io
import json
import tempfile
import shutil
from datetime import datetime, timezone

import numpy as np
from flask import (
    Blueprint, request, jsonify, Response, g,
    render_template, make_response,
)

# Existing project auth — same decorators used by /dicom/analyze
from api_auth import require_api_key
from dicom_customer import dicom_profile_required

from .pipeline import run_pipeline
from .analyzers import (
    max_severity, supported_body_parts, group_series, load_volume,
)
from .analyzers._spine_common import (
    select_best_t2_axsag, resample_sag_patient_coords,
)

# Report generator for /spine_report (lives at repo root)
from generate_report import generate_report


preimpression_bp = Blueprint('preimpression', __name__)


# ============================================================================
# Endpoints
# ============================================================================
@preimpression_bp.route('/supported-body-parts', methods=['GET'])
def supported_bp():
    """List all body-part codes the registry recognizes. Public; no auth."""
    return jsonify({'body_parts': supported_body_parts()})


@preimpression_bp.route('/preimpression', methods=['POST'])
@require_api_key
@dicom_profile_required
def preimpression():
    """Run the pre-impression pipeline on an uploaded DICOM zip.

    Auth handled by decorators above. g.customer is loaded by
    @dicom_profile_required and contains the customer's IP-protection profile.
    """
    customer = getattr(g, 'customer', None)

    if 'dicom' not in request.files:
        return jsonify({'error': 'missing field "dicom"'}), 400

    f = request.files['dicom']
    fmt = request.args.get('format', 'json').lower()
    body_part_override = request.args.get('body_part')
    include_boundaries = request.args.get('include_boundaries', 'false').lower() == 'true'

    with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
        f.save(tmp.name)
        tmp_path = tmp.name

    try:
        if fmt == 'visualization':
            work = tempfile.mkdtemp(prefix='preimp_viz_')
            try:
                result = run_pipeline(
                    tmp_path,
                    body_part_override=body_part_override,
                    work_dir=work, keep_work=True,
                )
                png_bytes = render_visualization(result, work)
                return Response(png_bytes, mimetype='image/png')
            finally:
                shutil.rmtree(work, ignore_errors=True)
        else:
            result = run_pipeline(
                tmp_path,
                body_part_override=body_part_override,
            )
    except Exception as e:
        return jsonify({'error': 'pipeline failed', 'detail': str(e)}), 500
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    # IP-protection sanitization based on customer profile
    result = _sanitize_for_response(result, customer, include_boundaries)

    if fmt == 'markdown':
        return Response(render_markdown(result), mimetype='text/markdown')
    if fmt == 'text':
        return Response(render_text(result), mimetype='text/plain')
    return jsonify(result)


# ============================================================================
# /spine_report — user-facing HTML report endpoint (no auth, browser flow)
# ============================================================================
@preimpression_bp.route('/spine_report', methods=['GET', 'POST'])
def spine_report():
    """Browser-facing endpoint that runs the k7 measurement pipeline and
    returns the rendered HTML report inline.

    GET  → renders the upload form (templates/spine_report_upload.html).
    POST → accepts a multipart upload (field name 'dicom'), runs the
           pipeline with body_part_override='cervical_spine_k7', passes
           the result through generate_report(), and returns the HTML
           inline with Content-Type text/html.

    Intentionally open (no @require_api_key) — this is a user-facing
    browser tool, not an API consumer.
    """
    if request.method == 'GET':
        return render_template('spine_report_upload.html')

    f = request.files.get('dicom')
    if not f or not f.filename:
        return ('No file uploaded. Please choose a DICOM .zip file.',
                400, {'Content-Type': 'text/plain; charset=utf-8'})
    if not f.filename.lower().endswith('.zip'):
        return ('Uploaded file must be a .zip archive.',
                400, {'Content-Type': 'text/plain; charset=utf-8'})

    tmp_dir = tempfile.mkdtemp(prefix='spine_report_')
    try:
        zip_path = os.path.join(tmp_dir, 'study.zip')
        f.save(zip_path)

        try:
            result = run_pipeline(
                zip_path,
                body_part_override='cervical_spine_k7',
            )
        except Exception as e:
            return (f'Pipeline error: {e}',
                    500, {'Content-Type': 'text/plain; charset=utf-8'})

        if not result:
            return ('Pipeline returned no result.',
                    500, {'Content-Type': 'text/plain; charset=utf-8'})

        html_path = os.path.join(tmp_dir, 'report.html')
        try:
            generate_report(result, zip_path, html_path)
        except Exception as e:
            return (f'Report generation failed: {e}',
                    500, {'Content-Type': 'text/plain; charset=utf-8'})

        with open(html_path, 'r', encoding='utf-8') as fh:
            html_text = fh.read()

        resp = make_response(html_text)
        resp.headers['Content-Type'] = 'text/html; charset=utf-8'
        return resp
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ============================================================================
# IP-protection: sanitize the result before returning to caller
# ============================================================================
# These keys are stripped UNLESS include_boundaries=true is requested AND
# the customer profile permits it.
_DETAIL_KEYS_PER_MARKER = {
    'cord_boundary_3d', 'canal_boundary_3d', 'radial_angles_rad',
    'cord_radii_mm', 'canal_radii_mm', 'midline_col_per_row',
}
# These keys are stripped from the top-level result regardless.
_DETAIL_KEYS_TOP_LEVEL = {
    'preimpression_traceback',  # internal debug only
}


def _sanitize_for_response(result, customer, include_boundaries):
    """Strip implementation-detail fields from the response unless the
    customer profile permits them.

    customer is a dict from g.customer set by @dicom_profile_required.
    Expected keys (best-effort — function tolerates missing keys):
      - allow_full_response: bool — permit boundary polygons etc.
    """
    if not isinstance(result, dict):
        return result

    allow_full = bool((customer or {}).get('allow_full_response', False))
    keep_boundaries = include_boundaries and allow_full

    # Top-level strip
    for k in _DETAIL_KEYS_TOP_LEVEL:
        result.pop(k, None)

    if keep_boundaries:
        return result

    # Strip per-marker boundary arrays
    for marker in result.get('markers', []):
        for k in _DETAIL_KEYS_PER_MARKER:
            marker.pop(k, None)

    # Strip per-slice deep details
    for s in result.get('slice_measurements', []):
        for k in _DETAIL_KEYS_PER_MARKER:
            s.pop(k, None)

    # Brain: strip per-row midline arrays
    if 'brain_findings' in result:
        bf = result['brain_findings']
        flair = bf.get('flair_lesion_summary')
        if isinstance(flair, dict):
            flair.pop('lesions_by_slice', None)

    # Breast: keep top 5 masses only
    if 'breast_findings' in result:
        bf = result['breast_findings']
        if 'masses' in bf and isinstance(bf['masses'], list):
            bf['masses'] = bf['masses'][:5]

    return result


# ============================================================================
# Renderers
# ============================================================================
def render_text(r):
    """Plain-text pre-impression for queue display."""
    if r.get('status') in ('INSUFFICIENT_DATA', 'UNSUPPORTED_BODY_PART'):
        return f"{r.get('status')}: {r.get('reason') or r.get('detected_body_part')}\n"

    out = []
    out.append('=' * 72)
    bp = r.get('body_part_label', 'unknown')
    date = r.get('study', {}).get('date', '')
    out.append(f"  PRE-IMPRESSION — {bp.upper()} / {date}")
    out.append('=' * 72)
    out.append(f"Status: {r['status']}")
    out.append(f"Body part: {r.get('detected_body_part', '?')} "
               f"({r.get('body_part_source', '?')})")
    out.append(f"Study: {r['study'].get('description', '')}")
    out.append(f"Scanner: {r['scanner'].get('manufacturer', '')} "
               f"{r['scanner'].get('model', '')}, "
               f"{r['scanner'].get('field_strength', '')}T")

    counts = r['impression']['counts']
    out.append('')
    out.append(f"Findings: {counts.get('critical', 0)} critical, "
               f"{counts.get('moderate', 0)} moderate, "
               f"{counts.get('finding', 0)} finding")

    if r.get('level_summaries'):
        out.append('')
        out.append('PER-LEVEL MEASUREMENTS')
        out.append('-' * 72)
        out.append(f"{'Level':<10} {'n':>2}  {'cord_a':>6}  {'min_sp':>6}  "
                   f"{'mean_sp':>7}  {'asym':>7}  {'L/R':>10}  status")
        for ls in r['level_summaries']:
            sev = max_severity(ls.get('flags', []))
            ams = '%+.3f' % ls['asym_lr_mean']
            lr = '%4.2f/%4.2f' % (ls['left_space_mm'], ls['right_space_mm'])
            out.append('%-10s %2d  %6.0f  %6.2f  %7.2f  %7s  %10s  %s' % (
                ls['level'], ls['n_slices'], ls['cord_area_mean_mm2'],
                ls['space_min_mm'], ls['space_mean_mm'], ams, lr, sev,
            ))

    if r.get('brain_findings'):
        bf = r['brain_findings']
        out.append('')
        out.append('BRAIN FINDINGS')
        out.append('-' * 72)
        out.append(f"  Slices analyzed:    {bf.get('n_brain_slices_analyzed', '?')}")
        out.append(f"  Max midline shift:  {bf.get('max_midline_shift_mm', 0):+.2f} mm "
                   f"at z={bf.get('max_shift_at_z_mm', 0):.0f}")
        out.append(f"  Ventricle volumes:  L={bf.get('total_left_ventricle_mm2', 0):.0f} mm², "
                   f"R={bf.get('total_right_ventricle_mm2', 0):.0f} mm²")
        out.append(f"  Ventricle asym:     {bf.get('ventricle_asym_overall', 0):+.3f}")
        if bf.get('flair_lesion_summary'):
            f = bf['flair_lesion_summary']
            out.append(f"  FLAIR lesions:      {f['lesion_count']} foci, "
                       f"total area {f['lesion_total_area_mm2']:.0f} mm²")

    flags = r['impression'].get('flags', [])
    if flags:
        out.append('')
        out.append('FLAGS')
        out.append('-' * 72)
        for f in flags:
            out.append(f"  [{f['severity']:<8}] {f.get('level', '-'):<10} {f['label']}")

    out.append('')
    out.append(f"Pipeline: {r.get('pipeline_version', '')} — "
               f"{r['timing_ms']['total']:.0f} ms total")
    out.append(f"Generated: {r.get('generated_at', '')}")
    out.append('')
    out.append('AI pre-impression — radiologist interpretation required.')
    return '\n'.join(out)


def render_markdown(r):
    """Markdown pre-impression for richer review interfaces."""
    if r.get('status') in ('INSUFFICIENT_DATA', 'UNSUPPORTED_BODY_PART'):
        return (f"# Pre-impression\n\n**{r.get('status')}** — "
                f"{r.get('reason') or r.get('detected_body_part')}\n")

    md = []
    bp = r.get('body_part_label', 'unknown')
    md.append(f"# Pre-impression — {bp.replace('_', ' ').title()}")
    md.append('')
    md.append(f"**Status:** `{r['status']}`")
    md.append(f"**Detected body part:** {r.get('detected_body_part', '?')} "
              f"({r.get('body_part_source', '?')})")
    md.append(f"**Study date:** {r['study'].get('date', '—')}")
    md.append(f"**Description:** {r['study'].get('description', '—')}")
    md.append(f"**Scanner:** {r['scanner'].get('manufacturer', '')} "
              f"{r['scanner'].get('model', '')}, "
              f"{r['scanner'].get('field_strength', '')}T")
    md.append('')

    md.append('## Findings tally')
    md.append('')
    counts = r['impression']['counts']
    md.append('| Severity | Count |')
    md.append('|----------|-------|')
    md.append(f"| critical | {counts.get('critical', 0)} |")
    md.append(f"| moderate | {counts.get('moderate', 0)} |")
    md.append(f"| finding  | {counts.get('finding', 0)}  |")
    md.append(f"| normal   | {counts.get('normal', 0)}   |")
    md.append('')

    flags = r['impression'].get('flags', [])
    if flags:
        md.append('## Notable findings')
        md.append('')
        for f in flags:
            md.append(f"- **{f['severity']}** — {f.get('level', '-')}: {f['label']}")
        md.append('')

    if r.get('level_summaries'):
        md.append('## Per-level measurements')
        md.append('')
        md.append('| Level | n | Cord area mm² | Min space | Mean space | '
                  'Asym L-R | L space | R space | Status |')
        md.append('|-------|---|---------------|-----------|------------|'
                  '----------|---------|---------|--------|')
        for ls in r['level_summaries']:
            sev = max_severity(ls.get('flags', []))
            md.append(f"| {ls['level']} | {ls['n_slices']} | "
                      f"{ls['cord_area_mean_mm2']:.0f} | "
                      f"{ls['space_min_mm']:.2f} | "
                      f"{ls['space_mean_mm']:.2f} | "
                      f"{ls['asym_lr_mean']:+.3f} | "
                      f"{ls['left_space_mm']:.2f} | "
                      f"{ls['right_space_mm']:.2f} | "
                      f"{sev} |")
        md.append('')

    md.append('---')
    md.append('')
    md.append(f"*AI pre-impression generated {r.get('generated_at', '')}. "
              f"Radiologist interpretation required.*")
    return '\n'.join(md)


def render_visualization(result, work_dir):
    """Render the per-body-part 3D registration view as PNG bytes.

    Spine: 3-panel sagittal + top-down + severity profile.
    Brain: 3-panel midline shift + ventricle asym + summary.
    Joints/breast: status card placeholder (not yet implemented).
    """
    # Defer matplotlib import — only loaded when viz is requested
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec

    bp = result.get('body_part_label', '')
    if bp in ('cervical_spine', 'thoracic_spine', 'lumbar_spine'):
        return _render_spine_viz(result, work_dir, plt, GridSpec)
    elif bp == 'brain':
        return _render_brain_viz(result, work_dir, plt, GridSpec)
    else:
        return _render_status_card(result, plt)


def _render_status_card(result, plt):
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.text(0.5, 0.5,
            f"Status: {result.get('status', '?')}\n"
            f"Body part: {result.get('detected_body_part', '?')}\n"
            f"{result.get('reason', '')}",
            ha='center', va='center', fontsize=14)
    ax.axis('off')
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight')
    plt.close(fig)
    return buf.getvalue()


def _render_spine_viz(result, work_dir, plt, GridSpec):
    """3-panel spine view."""
    series = group_series(work_dir)
    _, sag_t2 = select_best_t2_axsag(series)
    sag_items = load_volume(sag_t2['files']) if sag_t2 else []

    pv = None; z_range = None; y_range = None
    if sag_items:
        mid_idx = int(np.argmin([abs(it['ipp'][0]) for it in sag_items]))
        bp = result.get('body_part_label', 'cervical_spine')
        if bp == 'cervical_spine':
            z_range = np.arange(-90, 110, 0.3)
            y_range = np.arange(-50, 60, 0.3)
        elif bp == 'thoracic_spine':
            z_range = np.arange(-300, 100, 0.4)
            y_range = np.arange(-50, 60, 0.4)
        else:
            z_range = np.arange(-200, 200, 0.4)
            y_range = np.arange(-60, 80, 0.4)
        pv = resample_sag_patient_coords(sag_items[mid_idx], z_range, y_range)

    markers = result.get('markers', [])
    levels = result.get('levels_detected', {})

    fig = plt.figure(figsize=(20, 12))
    gs = GridSpec(2, 3, figure=fig, height_ratios=[1.4, 1])

    ax_sag = fig.add_subplot(gs[:, 0:2])
    if pv is not None and (pv > 30).any():
        dl, dh = np.percentile(pv[pv > 30], [1, 99])
        ax_sag.imshow(pv, cmap='gray', vmin=dl, vmax=dh,
                      extent=[z_range[0], z_range[-1], y_range[-1], y_range[0]],
                      aspect='equal', origin='upper')

    sev_color = {'CRITICAL': '#cc0000', 'MODERATE': '#ee8800',
                 'FINDING':  '#ddaa00', 'NORMAL':   '#33aa33'}
    for m in markers:
        z = m['cord_xyz_mm'][2]
        cy = m['cord_xyz_mm'][1]
        cb = np.array(m.get('cord_boundary_3d', []))
        nb = np.array(m.get('canal_boundary_3d', []))
        if cb.size:
            ax_sag.plot([z, z], [cb[:, 1].min(), cb[:, 1].max()],
                        color='red', linewidth=3, alpha=0.6)
        if nb.size:
            ax_sag.plot([z, z], [nb[:, 1].min(), nb[:, 1].max()],
                        color='cyan', linewidth=1, alpha=0.4)
        col = sev_color.get(m.get('severity', 'NORMAL'), 'red')
        edge = 'orange' if m.get('recovered') else 'black'
        ax_sag.scatter(z, cy, c=col, s=50,
                       edgecolors=edge, linewidths=1.0, zorder=10)

    for name, z in levels.items():
        if '-' in name:
            continue
        ax_sag.axvline(z, color='lime', alpha=0.4, linewidth=1)
        if y_range is not None:
            ax_sag.text(z, y_range[0]+1, name, color='lime', fontsize=10,
                        ha='center', fontweight='bold')

    ax_sag.set_xlabel('Patient z (mm)')
    ax_sag.set_ylabel('Patient y (mm)')
    ax_sag.set_title(f"3D markers — {result.get('body_part_label', 'spine')}")
    ax_sag.invert_yaxis()

    ax_top = fig.add_subplot(gs[0, 2])
    zs = [m['cord_xyz_mm'][2] for m in markers]
    xs = [m['cord_xyz_mm'][0] for m in markers]
    if zs:
        ax_top.plot(zs, xs, 'r.-', markersize=8, alpha=0.7)
    ax_top.axhline(0, color='gray', alpha=0.3)
    ax_top.set_xlabel('Patient z (mm)')
    ax_top.set_ylabel('Patient x (mm) [+x = LEFT]')
    ax_top.set_title('Top-down: lateral cord deviation')
    ax_top.invert_xaxis()
    ax_top.grid(alpha=0.3)

    ax_sev = fig.add_subplot(gs[1, 2])
    smin = [m['space_min_mm'] for m in markers]
    asym = [m['asym_lr'] for m in markers]
    if zs:
        ax_sev.plot(zs, smin, 'g.-', markersize=6)
    ax_sev_t = ax_sev.twinx()
    if zs:
        ax_sev_t.plot(zs, asym, 'b.-', markersize=6, alpha=0.6)
    ax_sev.axhline(2.5, color='orange', alpha=0.4, linestyle='--')
    ax_sev.axhline(1.5, color='red', alpha=0.4, linestyle='--')
    ax_sev.axhline(0.5, color='darkred', alpha=0.4, linestyle='--')
    ax_sev_t.axhline(0, color='gray', alpha=0.3)
    ax_sev.set_xlabel('Patient z (mm)')
    ax_sev.set_ylabel('min space (mm)', color='green')
    ax_sev_t.set_ylabel('asym (L-R)/(L+R)', color='blue')
    ax_sev.set_title('Per-slice severity')
    ax_sev.invert_xaxis()
    ax_sev.grid(alpha=0.3)

    fig.suptitle(
        f"Pre-impression 3D registration — status: {result.get('status', '')}",
        fontsize=14, fontweight='bold', y=1.00,
    )
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=120, bbox_inches='tight')
    plt.close(fig)
    return buf.getvalue()


def _render_brain_viz(result, work_dir, plt, GridSpec):
    """3-panel brain view."""
    markers = result.get('markers', [])
    bf = result.get('brain_findings', {})

    fig = plt.figure(figsize=(18, 10))
    gs = GridSpec(2, 3, figure=fig)

    ax_shift = fig.add_subplot(gs[0, 0])
    if markers:
        zs = [m['midline_xyz_mm'][2] for m in markers]
        shifts = [m['midline_shift_mm'] for m in markers]
        ax_shift.plot(zs, shifts, 'r.-', markersize=8)
        ax_shift.axhline(0, color='gray', alpha=0.4)
        for thr in (1.0, -1.0, 3.0, -3.0, 5.0, -5.0):
            ax_shift.axhline(thr, color='gray', alpha=0.2, linestyle='--')
    ax_shift.set_xlabel('Patient z (mm)')
    ax_shift.set_ylabel('Midline shift (mm)\n(+ = patient left)')
    ax_shift.set_title('Midline shift profile')
    ax_shift.grid(alpha=0.3)

    ax_va = fig.add_subplot(gs[0, 1])
    if markers:
        zs = [m['midline_xyz_mm'][2] for m in markers]
        vas = [m['ventricle_asym_lr'] for m in markers]
        bas = [m['brain_asym_lr'] for m in markers]
        ax_va.plot(zs, vas, 'b.-', markersize=8, label='ventricle asym')
        ax_va.plot(zs, bas, 'g.-', markersize=6, alpha=0.5, label='brain asym')
    ax_va.axhline(0, color='gray', alpha=0.4)
    ax_va.set_xlabel('Patient z (mm)')
    ax_va.set_ylabel('Asym (L-R)/(L+R)')
    ax_va.set_title('Ventricle and brain asymmetry')
    ax_va.legend(fontsize=8)
    ax_va.grid(alpha=0.3)

    ax_summary = fig.add_subplot(gs[0, 2])
    ax_summary.axis('off')
    txt = []
    txt.append(f"Status: {result.get('status', '')}")
    txt.append('')
    txt.append(f"Slices: {bf.get('n_brain_slices_analyzed', '?')}")
    txt.append(f"Max midline shift: {bf.get('max_midline_shift_mm', 0):+.2f} mm")
    txt.append(f"  at z = {bf.get('max_shift_at_z_mm', 0):.0f}")
    txt.append('')
    txt.append(f"Ventricle asym: {bf.get('ventricle_asym_overall', 0):+.3f}")
    if bf.get('flair_lesion_summary'):
        f = bf['flair_lesion_summary']
        txt.append('')
        txt.append(f"FLAIR lesions: {f['lesion_count']} foci")
    ax_summary.text(0.05, 0.95, '\n'.join(txt),
                    ha='left', va='top', fontsize=11, family='monospace',
                    transform=ax_summary.transAxes)
    ax_summary.set_title('Brain summary')

    ax_flags = fig.add_subplot(gs[1, :])
    ax_flags.axis('off')
    flags = result.get('impression', {}).get('flags', [])
    flag_lines = ['  (no findings above threshold)'] if not flags else [
        f"  [{f['severity']:<8}] {f.get('level', '-'):<10} {f.get('label', '')}"
        for f in flags[:20]
    ]
    ax_flags.text(0.02, 0.95, 'FINDINGS\n' + '\n'.join(flag_lines),
                  ha='left', va='top', fontsize=11, family='monospace',
                  transform=ax_flags.transAxes)

    fig.suptitle(
        f"Pre-impression — brain — status: {result.get('status', '')}",
        fontsize=14, fontweight='bold',
    )
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=120, bbox_inches='tight')
    plt.close(fig)
    return buf.getvalue()
