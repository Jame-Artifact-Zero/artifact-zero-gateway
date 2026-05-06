"""
analyzers/knee.py
=================
Knee MRI analyzer.

Findings detected:
  - Joint effusion (suprapatellar pouch / parapatellar recesses)
  - Bone marrow edema (femoral condyles, tibial plateau, patella)

Not yet implemented (would need region segmentation):
  - Specific meniscal tear morphology
  - ACL/PCL/MCL/LCL signal abnormality
  - Cartilage thickness mapping
  - Baker's cyst (separate from joint effusion)

Sequence preference: STIR > PD FS > T2 FS > T2 > PD.
"""
from ._joint_common import GenericJointAnalyzer


class KneeAnalyzer(GenericJointAnalyzer):
    body_part_codes = ('KNEE', 'KNEE_LT', 'KNEE_RT', 'KNEE_LEFT', 'KNEE_RIGHT')
    body_part_label = 'knee'
    effusion_thresholds = {
        'critical_mm3': 30000, 'moderate_mm3': 10000, 'finding_mm3': 3000,
    }
    marrow_edema_thresholds = {
        'critical_mm3': 5000, 'moderate_mm3': 1500, 'finding_mm3': 300,
    }
    min_anatomy_area_mm2 = 2000
