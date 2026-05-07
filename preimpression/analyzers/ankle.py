"""
analyzers/ankle.py
==================
Ankle MRI analyzer.

Findings detected:
  - Joint effusion (tibiotalar, subtalar)
  - Bone marrow edema (talus, distal tibia, distal fibula, calcaneus)

Not yet implemented:
  - ATFL/CFL/PTFL ligament signal/discontinuity
  - Achilles, posterior tibial, peroneal tendon abnormality
  - Talar dome osteochondral defects (visible as marrow edema, but not 
    distinguished from generic edema)
  - Sinus tarsi syndrome

Sequence preference: STIR > PD FS > T2 FS.
"""
from ._joint_common import GenericJointAnalyzer


class AnkleAnalyzer(GenericJointAnalyzer):
    body_part_codes = ('ANKLE', 'ANKLE_LT', 'ANKLE_RT', 'ANKLE_LEFT', 'ANKLE_RIGHT')
    body_part_label = 'ankle'
    effusion_thresholds = {
        'critical_mm3': 12000, 'moderate_mm3': 4000, 'finding_mm3': 1000,
    }
    marrow_edema_thresholds = {
        'critical_mm3': 3000, 'moderate_mm3': 1000, 'finding_mm3': 200,
    }
    min_anatomy_area_mm2 = 1500
