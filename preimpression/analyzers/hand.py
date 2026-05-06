"""
analyzers/hand.py
=================
Hand MRI analyzer.

Findings detected:
  - Soft tissue fluid (tenosynovitis pattern — fluid surrounding finger 
    flexor / extensor tendons)
  - Bone marrow edema (metacarpals, phalanges)
  - Joint effusion (MCP, PIP, DIP joints)

Particularly important to flag:
  - MCP/PIP synovial enhancement pattern (rheumatoid arthritis)
  - Phalangeal marrow edema (stress, infection)

Not yet implemented:
  - Pulley injury (A2/A4 — flexor pulley rupture)
  - Boutonnière / swan-neck deformity tracking
  - Gamekeeper's / skier's thumb (UCL of thumb)
  - Trigger finger localization

Sequence preference: STIR > PD FS > T2 FS. Hand bones are very small;
marrow edema thresholds are the most sensitive of any joint analyzer.
"""
from ._joint_common import GenericJointAnalyzer


class HandAnalyzer(GenericJointAnalyzer):
    body_part_codes = ('HAND', 'HAND_LT', 'HAND_RT', 'HAND_LEFT', 'HAND_RIGHT',
                       'FINGER', 'FINGERS', 'DIGITS')
    body_part_label = 'hand'
    effusion_thresholds = {
        'critical_mm3': 3000, 'moderate_mm3': 800, 'finding_mm3': 200,
    }
    marrow_edema_thresholds = {
        'critical_mm3': 800, 'moderate_mm3': 200, 'finding_mm3': 50,
    }
    min_anatomy_area_mm2 = 600
