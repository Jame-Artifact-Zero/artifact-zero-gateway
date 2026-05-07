"""
analyzers/elbow.py
==================
Elbow MRI analyzer.

Findings detected:
  - Joint effusion (anterior recess, posterior recess, radial bursa)
  - Bone marrow edema (distal humerus, proximal radius/ulna, capitellum,
    olecranon)

Not yet implemented:
  - UCL/RCL signal/discontinuity (medial/lateral collateral ligament)
  - Common flexor / extensor tendinosis ("medial epicondylitis" / "lateral 
    epicondylitis" — golfer's / tennis elbow)
  - Distal biceps tendon insertion abnormality
  - Triceps tendon at olecranon
  - Olecranon bursitis localization

Sequence preference: STIR > PD FS > T2 FS.
"""
from ._joint_common import GenericJointAnalyzer


class ElbowAnalyzer(GenericJointAnalyzer):
    body_part_codes = ('ELBOW', 'ELBOW_LT', 'ELBOW_RT', 'ELBOW_LEFT', 'ELBOW_RIGHT')
    body_part_label = 'elbow'
    effusion_thresholds = {
        'critical_mm3': 10000, 'moderate_mm3': 3000, 'finding_mm3': 800,
    }
    marrow_edema_thresholds = {
        'critical_mm3': 3000, 'moderate_mm3': 800, 'finding_mm3': 200,
    }
    min_anatomy_area_mm2 = 2000
