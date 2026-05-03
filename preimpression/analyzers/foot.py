"""
analyzers/foot.py
=================
Foot MRI analyzer.

Findings detected:
  - Soft tissue edema / fluid (plantar fascia thickening shows as fluid 
    along the calcaneus origin)
  - Bone marrow edema (metatarsals — stress fractures, sesamoids,
    tarsal bones, calcaneus)

Not yet implemented:
  - Plantar fascia thickness measurement (would need fascia tracking)
  - Morton's neuroma localization (interspace specific)
  - Lisfranc ligament integrity
  - Sesamoid pathology localization

Sequence preference: STIR > PD FS > T2 FS.

Note: foot has small bones close together, so marrow edema findings tend 
to be small but clinically significant (stress fracture risk). Threshold
is more sensitive than knee.
"""
from ._joint_common import GenericJointAnalyzer


class FootAnalyzer(GenericJointAnalyzer):
    body_part_codes = ('FOOT', 'FOOT_LT', 'FOOT_RT', 'FOOT_LEFT', 'FOOT_RIGHT', 'FT')
    body_part_label = 'foot'
    effusion_thresholds = {
        'critical_mm3': 8000, 'moderate_mm3': 2500, 'finding_mm3': 700,
    }
    marrow_edema_thresholds = {
        'critical_mm3': 2000, 'moderate_mm3': 500, 'finding_mm3': 100,
    }
    min_anatomy_area_mm2 = 1000
