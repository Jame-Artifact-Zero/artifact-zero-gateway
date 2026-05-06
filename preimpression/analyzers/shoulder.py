"""
analyzers/shoulder.py
=====================
Shoulder MRI analyzer.

Findings detected:
  - Glenohumeral joint effusion / subdeltoid bursa fluid
  - Bone marrow edema (humeral head, glenoid, acromion)

Not yet implemented:
  - Rotator cuff tendon (supraspinatus/infraspinatus/subscapularis/teres minor) 
    tear morphology (full vs partial, location)
  - Labral tear (Bankart, SLAP)
  - Biceps tendon abnormality (subluxation, tear)
  - AC joint arthrosis grading

Sequence preference: T2 FS > PD FS > STIR. T2 FS is most common in shoulder
because rotator cuff tears stand out as bright fluid signal in tendon.
"""
from ._joint_common import GenericJointAnalyzer


class ShoulderAnalyzer(GenericJointAnalyzer):
    body_part_codes = ('SHOULDER', 'SHOULDER_LT', 'SHOULDER_RT',
                       'SHOULDER_LEFT', 'SHOULDER_RIGHT', 'SHLDR')
    body_part_label = 'shoulder'
    effusion_thresholds = {
        'critical_mm3': 15000, 'moderate_mm3': 5000, 'finding_mm3': 1500,
    }
    marrow_edema_thresholds = {
        'critical_mm3': 4000, 'moderate_mm3': 1000, 'finding_mm3': 250,
    }
    min_anatomy_area_mm2 = 2500
