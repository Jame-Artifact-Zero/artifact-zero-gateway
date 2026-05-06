"""
analyzers/wrist.py
==================
Wrist MRI analyzer.

Findings detected:
  - Joint effusion (radiocarpal, midcarpal, distal radioulnar joint)
  - Bone marrow edema (carpal bones — scaphoid, lunate, triquetrum,
    pisiform, trapezium, trapezoid, capitate, hamate; distal radius/ulna)
  - Soft tissue fluid (carpal tunnel, ganglion cysts)

Particularly important to flag:
  - Scaphoid marrow edema → occult fracture / AVN risk
  - Lunate marrow edema → Kienböck's disease

Not yet implemented:
  - TFCC integrity (would need cartilage region segmentation)
  - Scapholunate / lunotriquetral ligament tear
  - Carpal tunnel median nerve cross-sectional area
  - Ganglion cyst tracking

Sequence preference: STIR > PD FS > T2 FS. Carpal bones are small so
marrow edema thresholds are sensitive.
"""
from ._joint_common import GenericJointAnalyzer


class WristAnalyzer(GenericJointAnalyzer):
    body_part_codes = ('WRIST', 'WRIST_LT', 'WRIST_RT', 'WRIST_LEFT', 'WRIST_RIGHT')
    body_part_label = 'wrist'
    effusion_thresholds = {
        'critical_mm3': 5000, 'moderate_mm3': 1500, 'finding_mm3': 400,
    }
    marrow_edema_thresholds = {
        'critical_mm3': 1500, 'moderate_mm3': 400, 'finding_mm3': 80,
    }
    min_anatomy_area_mm2 = 1000
