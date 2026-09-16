# Reputation Farmer Multibox BT - Faction / Title Farmer (Multibox build).
# Split from Reputation Farmer BT.py while the SharedCommandType.GetBlessing
# dispatch in Widgets/System/Messaging.py is broken (elevated to the
# messaging-layer owners, not patched here). Non-faction shrine blessings fan
# out through TargetNearestAndAutoDialog (TakeDialogWithTarget / SendDialog
# receivers), which ARE wired, instead of the dead GetBlessing path. Re-merge
# into the solo file once GetBlessing is fixed.
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

import os
import json
import time
import types
import PySystem

from Py4GWCoreLib import GLOBAL_CACHE, HeroType, Map, Player, PyImGui
from Py4GWCoreLib.BottingTree import BottingTree
from Py4GWCoreLib.Listeners import Listeners
from Py4GWCoreLib.py4gwcorelib_src.BehaviorTree import BehaviorTree
from Py4GWCoreLib.enums_src.Multiboxing_enums import SharedCommandType
from Py4GWCoreLib.enums_src.Title_enums import TitleID, TITLE_TIERS
from Py4GWCoreLib.routines_src.BehaviourTrees import BT as CoreBT
from Sources.ApoSource.ApoBottingLib import wrappers as BT
from Sources.aC_Scripts.aC_api.Verify_Blessing import Blessings

MODULE_NAME = "Reputation Farmer Multibox BT"
MODULE_ICON = "Assets/Textures/Skill_Icons/[1887] - Lightbringers Insight.jpg"
ROUTINE_NAME = "ReputationFarmerMultiboxSequence"

FACTION_GOAL = 10_000
VQ_MAX_RUNS = 6
RUN_RETRY_TIMEOUT_MS = 30 * 60 * 1000
RESIGN_RETRY_TIMEOUT_MS = 3 * 60 * 1000
LEADER_DEATH_GRACE_MS = 30 * 1000
RESIGN_SUPPRESS_STALE_MS = 120 * 1000
DIAGNOSTIC_HEARTBEAT_MS = 15 * 1000
BLESSING_GOLD = 500

# Hero team setup
BOT_BASE_DIR = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
PARTY_FORMATION_CONFIG_PATH = os.path.join(BOT_BASE_DIR, "Reputation Farmer Party Formation.json")

HERO_AGGRESSIVE_MODE = 1

TEAM_PRESET_SIZES = [4, 6, 8]
TEAM_PRESET_SLOT_COUNTS = {4: 3, 6: 5, 8: 7}

HERO_OPTIONS: List[HeroType] = [HeroType.None_] + [hero for hero in HeroType if hero != HeroType.None_]
HERO_OPTION_LABELS = [hero.name.replace("_", " ") if hero != HeroType.None_ else "<Empty>" for hero in HERO_OPTIONS]
HERO_ID_TO_OPTION_INDEX = {int(hero.value): index for index, hero in enumerate(HERO_OPTIONS)}

# Default skillbar templates per hero.
DEFAULT_HERO_TEMPLATES: dict = {
    HeroType.Norgu: "OQBDAawDSvAIgcQ5ZkAFgZAEBA",
    HeroType.Gwen: "OQhkAsC8gFKzJIHM9MdDBcaG4iB",
    HeroType.Vekk: "OgVDI8gsS5AnATPmOHgCAZAFBA",
    HeroType.MasterOfWhispers: "OABDUshnSyBVBoBKgbhVVfCWCA",
    HeroType.Olias: "OAhjQoGYIP3hhWVVaO5EeDTqNA",
    HeroType.Ogden: "OwUUMsG/E4SNgbE3N3ETfQgZAMEA",
    HeroType.Razah: "OAWjMMgMJPYTr3jLcCNdmZgeAA",
    HeroType.Xandra: "OAWjMMgMJPYTr3jLcCNdmZgeAA",
    HeroType.ZhedShadowhoof: "OgVDI8gsS5AnATPmOHgCAZAFBA",
}


@dataclass
class PartyHeroSlot:
    hero_id: int = HeroType.None_.value
    template: str = ""

from Py4GWCoreLib.routines_src.behaviourtrees_src.constants.lists import (
    CONSUMABLE_UPKEEPS,
    CONSET_UPKEEPS,
)

PCON_UPKEEPS: Tuple[int, ...] = tuple(
    int(model_id) for model_id in CONSUMABLE_UPKEEPS if int(model_id) not in CONSET_UPKEEPS
)


class Faction(Enum):
    VANGUARD = "vanguard"
    ASURAN = "asuran"
    NORN = "norn"
    DELDRIMOR = "deldrimor"
    LUXON = "luxon"
    KURZICK = "kurzick"
    SUNSPEAR = "sunspear"
    LIGHTBRINGER = "lightbringer"


@dataclass
class Blessing:
    pos: Tuple[float, float]
    dialog_id: int = 0x84
    # Fire-and-forget dialog when False: no effect confirmation and no
    # "already confirmed" skip. The Deldrimor route takes only its first
    # shrine this way (dialog and move on); routes taking several same-type
    # shrines would need it too, since every shrine confirms against the same
    # effect-ID tuple and shrine 1's still-active blessing would make shrine
    # 2 look already-taken.
    confirm: bool = True


@dataclass
class RouteAction:
    """One extra step executed after a segment's kill path.

    Set exactly one field per instance; list order is execution order:
    a gadget interaction, a plain wait, an out-of-combat wait, or a loot
    pass. Used for dungeon mechanics (door lock, boss lock, chest) on the
    Deldrimor route; every other route leaves this empty.
    """
    name: str = ""
    gadget_pos: Optional[Tuple[float, float]] = None
    wait_ms: int = 0
    wait_out_of_combat: bool = False
    loot: bool = False


@dataclass
class RouteSegment:
    name: str = ""
    blessing: Optional[Blessing] = None
    path: Sequence[Tuple[float, float]] = ()
    actions: Sequence[RouteAction] = ()


@dataclass
class Route:
    key: str
    name: str
    icon: str
    title_id: int
    outpost_id: int
    explorable_id: int
    exit_pos: Tuple[float, float]
    exit_by_name: bool = False
    pre_path: Sequence[Tuple[float, float]] = ()
    blessing_points: Sequence[Blessing] = ()
    kill_path: Sequence[Tuple[float, float]] = ()
    segments: Sequence[RouteSegment] = ()
    bounty: bool = False
    bounty_pos: Optional[Tuple[float, float]] = None
    bounty_dialog: int = 0x85
    entry_dialog_id: int = 0


# Route data
# Vanguard - Dalada Uplands (Ebon Vanguard title).
VANGUARD_ROUTE = Route(
    key="vanguard",
    name="Vanguard",
    icon="[2233] - Ebon Battle Standard of Honor.jpg",
    title_id=TitleID.Ebon_Vanguard,
    outpost_id=648,      # Dalada Uplands outpost
    explorable_id=647,   # Dalada Uplands explorable
    exit_pos=(-15400.0, 13500.0),
    pre_path=[(-16016.0, 17340.0), (-15400.0, 13500.0)],
    segments=[
        RouteSegment(
            name="Vanguard Segment 1",
            blessing=Blessing((-14971.0, 11013.0)),
            path=[
                (-14350.5, 12790.6), (-17600.7, 10388.3), (-16649.0, 6485.4), (-16131.3, 2494.2),
                (-13528.1, -571.5), (-15663.4, -3959.4), (-18089.6, -7150.1), (-17921.5, -11167.4),
                (-15917.0, -14662.3), (-13390.84, -16843.04), (-12191.4, -16190.6), (-8482.2, -14675.8),
                (-7746.7, -18628.1), (-4699.0, -15996.0), (-734.2, -16733.1), (3209.2, -17521.2),
                (7204.8, -17236.8), (10660.3, -15173.9), (14231.2, -13323.1), (15486.11, -14122.26),
                (17868.1, -11540.7), (14280.7, -9705.3), (13958.0, -5657.5), (17851.7, -4510.7),
                (14141.2, -2985.1), (10104.9, -2608.4), (10392.6, 1429.8), (14414.1, 923.4),
                (16536.4, 4358.9), (17027.8, 8366.5), (14253.5, 11258.4), (12708.4, 14995.4),
                (8842.1, 16056.3), (5366.9, 18114.6), (2657.9, 15144.8), (-1025.2, 16731.2),
                (1142.8, 13355.0), (-2272.1, 11178.6), (-6246.7, 12038.8), (-8875.1, 15092.1),
                (-9545.32, 16453.30), (-10593.52, 14475.55), (-11859.57, 12183.40), (-9680.6, 11168.8),
                (-7630.3, 7678.4), (-3717.2, 8618.1), (-3227.72, 8829.67), (232.2, 9451.7),
                (4266.0, 9959.4), (8007.6, 8342.5), (4888.8, 5766.7), (1037.3, 4668.6),
                (-2887.1, 3697.4), (-6918.0, 4104.1), (-10897.1, 4922.3), (-14702.6, 6233.5),
                (-10898.6, 4878.2), (-9045.5, 1321.2), (-8657.0, -2712.6), (-5189.2, -611.5),
                (-1172.4, 95.6), (2474.3, 1913.7), (6476.9, 2343.3), (5489.0, -1545.9),
                (5552.4, -5596.4), (7189.7, -9305.8), (8261.67, -12055.48), (5228.1, -5784.1),
                (2164.1, -3177.7), (-1530.8, -4867.3), (156.3, -8499.8), (3819.1, -10133.5),
                (2167.7, -13796.2), (-1821.5, -14135.8), (-5747.9, -13218.7),
            ],
        ),
        RouteSegment(
            name="Vanguard Segment 2",
            blessing=Blessing((-2641.0, 449.0)),
            path=[
                (-1172.4, 95.6), (2474.3, 1913.7), (6476.9, 2343.3), (5489.0, -1545.9),
                (5552.4, -5596.4), (7189.7, -9305.8), (8261.67, -12055.48), (5228.1, -5784.1),
                (2164.1, -3177.7), (-1530.8, -4867.3), (156.3, -8499.8), (3819.1, -10133.5),
                (2167.7, -13796.2), (-1821.5, -14135.8), (-5747.9, -13218.7),
            ],
        ),
        RouteSegment(
            name="Vanguard Segment 3",
            blessing=Blessing((-3954.0, -11426.0)),
            path=[
                (-5747.9, -13218.7), (-9790.9, -13258.0), (-11047.5, -9448.2), (-7777.1, -7032.2),
                (-4638.2, -4496.5), (-1131.0, -2524.7), (1852.3, 163.3), (5104.8, 2594.2),
                (8307.3, 5060.4), (7509.3, 8998.1), (10537.1, 11668.0), (8091.5, 8492.2),
                (11725.8, 6705.3), (7964.3, 8157.4), (4666.3, 10422.2),
            ],
        ),
        RouteSegment(
            name="Vanguard Segment 4",
            blessing=Blessing((5884.0, 11749.0)),
            path=[
                (4666.3, 10422.2),
                (1772.7, 13212.8),
            ],
        ),
    ],
)

# Asuran - Magus Stones / Rata Sum (Asuran title).
# Full farm ported from "Asura title farm by Wick Divinus.py": one shrine
# blessing per leg and the complete kill path, including the final sweep that
# ends on the Krait Boss Warrior before the resign.
ASURAN_ROUTE = Route(
    key="asuran",
    name="Asuran",
    icon="[2372] - Edification.jpg",
    title_id=TitleID.Asuran,
    outpost_id=640,      # Rata Sum
    explorable_id=569,   # Magus Stones
    exit_pos=(-6062.0, -2688.0),
    segments=[
        RouteSegment(
            name="Asuran Segment 1",
            blessing=Blessing((14901.87, 13126.21)),
            path=[
                (18825, 6180), (18447, 4537), (18331, 2108), (17526, 143),
                (17205, -1355), (17542, -4865), (15562, -5524), (16270, -6288),
                (17501, -5545), (18111, -8030), (18409, -8474), (18613, -11799),
                (17154, -15669), (14250, -16744), (12186, -14139), (12540, -13440),
                (13234, -9948), (8875, -9065), (8647, -5852), (6939, -3629),
                (8711, -6046), (7616, -8978), (4671, -8699), (-5203, -8280),
                (1534, -5493), (1052, -7074), (-1029, -8724), (-3439, -10339),
                (-3024, -12586), (-742, -13786), (-2755, -14099), (-3393, -15633),
                (-4635, -16643), (-7814, -17796), (-10109, -17520), (-9111, -17237),
                (-10963, -15506), (-13975, -17857), (-11912, -10641), (-8760, -9933),
                (-14030, -9780), (-12368, -7330),
            ],
        ),
        RouteSegment(
            name="Asuran Segment 2",
            blessing=Blessing((-9317.0, -2618.0)),
            path=[
                (-12368, -7330), (-16527, -8175), (-17391, -5984),
                (-15704, -3996), (-16609, -2607), (-16480, 2522), (-17090, 5252),
                (-18640, 8724), (-18484, 12021), (-17180, 13093), (-15072, 14075),
                (-11888, 15628), (-12043, 18463), (-8876, 17415), (-4770, 20353),
                (-10970, 16860), (-9301, 15054), (-9942, 12561), (-9786, 10297),
                (-5379, 16642), (-2828, 18210), (-4246, 16728), (-2974, 14197),
                (-5228, 12475), (-6756, 12380), (-3468, 10837), (-3804, 8017),
                (-3288, 7276), (-1346, 12360),
            ],
        ),
        RouteSegment(
            name="Asuran Segment 3",
            blessing=Blessing((4835.0, 440.0)),
            path=[
                (-1346, 12360), (874, 14367), (3572, 13698),
                (5899, 14205), (7407, 11867), (9541, 9027), (12639, 7537),
                (9064, 7312), (7986, 4365), (8558, 2759), (10685, 3500),
                (10202, 5369), (8043, 5949), (7978, 3339), (6341, 3029),
                (5362, 3391), (7097, 92), (8943, -985), (10949, -2056),
                (13780, -5667), (10752, 991), (8193, -841), (3284, -1599),
                (-76, -1498), (578, 719), (1703, 3975), (316, 2489),
                (-1018, -1235), (-3195, -1538), (-6322, -2565), (-11414, 4055),
                (-7030, 8396), (-8689, 11227),
                # Final sweep: leftover Krait patrol and the Krait Boss Warrior.
                (4671, -8699), (-1018, -1235), (-6322, -2565), (-8760, -9933),
            ],
        ),
    ],
)

# Norn - Varajar Fells / Olafstead (Norn title).
NORN_ROUTE = Route(
    key="norn",
    name="Norn",
    icon="[2373] - Heart of the Norn.jpg",
    title_id=TitleID.Norn,
    outpost_id=645,      # Olafstead
    explorable_id=553,   # Varajar Fells
    exit_pos=(-1500.0, 1250.0),
    pre_path=[(-328.0, 1240.0), (-1500.0, 1250.0)],
    segments=[
        RouteSegment(
            name="Norn Approach - Shrine 1",
            path=[
                (-2484.73, 118.55), (-3059.12, -419.00), (-3301.01, -2008.23),
                (-2034, -4512),
            ],
        ),
        RouteSegment(
            name="Norn Blessing 1",
            blessing=Blessing((-1892.0, -4505.0)),
            path=[
                (-5278, -5771), (-5456, -7921), (-8793, -5837), (-14092, -9662),
                (-17260, -7906), (-21964, -12877), (-25341.00, -11957.00),
            ],
        ),
        RouteSegment(
            name="Norn Blessing 2",
            blessing=Blessing((-25341.0, -11957.0)),
            path=[
                (-22275, -12462), (-21671, -2163), (-19592, 772), (-13795, -751),
                (-17012, -5376), (-10606.23, -1625.26), (-12158.00, -4277.00),
            ],
        ),
        RouteSegment(
            name="Norn Blessing 3",
            blessing=Blessing((-12158.0, -4277.0)),
            path=[
                (-12071, -4274), (-8351, -2633), (-4362, -1610), (-4316, 4033),
                (-8809, 5639), (-14916, 2475), (-11204.00, 5479.00),
            ],
        ),
        RouteSegment(
            name="Norn Blessing 4",
            blessing=Blessing((-11204.0, 5479.0)),
            path=[
                (-11282, 5466), (-16051, 6492), (-16934, 11145), (-19378, 14555),
                (-22889.00, 14165.00),
            ],
        ),
        RouteSegment(
            name="Norn Blessing 5",
            blessing=Blessing((-22889.0, 14165.0)),
            path=[
                (-22751, 14163), (-15932, 9386), (-13777, 8097), (-2217.00, 14914.00),
            ],
        ),
        RouteSegment(
            name="Norn Blessing 6",
            blessing=Blessing((-2217.0, 14914.0)),
            path=[
                (-2290, 14879), (-1810, 4679), (-6911, 5240), (-15471, 6384),
                (-411, 5874), (2859, 3982), (4909, -4259), (7514, -6587),
                (3800, -6182), (7755, -11467), (15403, -4243),
            ],
        ),
        RouteSegment(
            name="Norn Continue Route - East",
            path=[
                (21597, -6798), (24522, -6532), (22883, -4248), (18606, -1894),
                (14969, -4048), (13599, -7339), (10056, -4967), (10147, -1630),
            ],
        ),
        RouteSegment(
            name="Norn Blessing 8",
            blessing=Blessing((8963.0, 4043.0)),
            path=[
                (9339.46, 3859.12), (15576, 7156),
            ],
        ),
        RouteSegment(
            name="Norn Blessing 9",
            blessing=Blessing((22838.0, 7914.0)),
            path=[
                (22961, 12757), (18067, 8766), (13311, 11917), (13714, 14520),
                (11126, 10443), (5575, 4696), (-503, 9182), (1582, 15275),
                (7857, 10409),
            ],
        ),
    ],
)

# Deldrimor - snowman dungeon via Sifhalla (Deldrimor title). Mirrors the
# original "Deldrimor title farm by Wick Divinus" flow: zone in through the
# entry dialog, clear each fight leg (VanquishNode), take the first shrine
# fire-and-forget and skip the later shrines, loot the key drop before the
# locked door, interact the door lock and the boss lock, wait out the boss
# window, loot the chest -- then the run's normal end-of-loop team resign
# fires.
DELDRIMOR_ROUTE = Route(
    key="deldrimor",
    name="Deldrimor",
    icon="[2424] - Stout-Hearted.jpg",
    title_id=TitleID.Deldrimor,
    outpost_id=639,
    explorable_id=701,
    exit_pos=(-23884.0, 13954.0),
    entry_dialog_id=0x84,
    segments=[
        RouteSegment(
            name="Deldrimor Fight Leg 1",
            blessing=Blessing((-14078.0, 15449.0), confirm=False),
            path=[
                (-14804, 10703), (-15628, 9589), (-17602, 6858), (-19769, 5046),
                (-16697.96, 1302.89), (-15090.34, 2057.10),
            ],
        ),
        RouteSegment(
            name="Deldrimor Fight Leg 2 (key drop)",
            path=[
                (-14450.00, 3411.00), (-13824.00, 924.00), (-13752.06, -504.66),
                (-12084.77, -1592.58), (-12745.70, -3899.97), (-13262.00, -7346.00),
                (-14891.95, -10069.69), (-9573.00, -10963.00), (-9703.92, -10948.97),
            ],
            actions=[
                RouteAction(name="Regroup And Loot Key Drop", wait_out_of_combat=True, loot=True),
            ],
        ),
        RouteSegment(
            name="Deldrimor Locked Door",
            path=[(-15756.00, -12335.00)],
            actions=[
                RouteAction(name="Door Lock", gadget_pos=(-15435.00, -12277.00)),
                RouteAction(name="Door Opens", wait_ms=3000),
            ],
        ),
        RouteSegment(
            name="Deldrimor Fight Leg 3",
            path=[
                (-17542.00, -14048.00), (-13088.00, -17749.00), (-13004.20, -17304.91),
            ],
            actions=[
                RouteAction(name="Regroup And Loot", wait_out_of_combat=True, loot=True),
            ],
        ),
        RouteSegment(
            name="Deldrimor Boss Trigger",
            path=[(-11136.00, -18043.00)],
            actions=[
                RouteAction(name="Boss Lock", gadget_pos=(-11136.00, -18043.00)),
                RouteAction(name="Boss Spawn", wait_ms=3000),
            ],
        ),
        RouteSegment(
            name="Deldrimor Chest",
            path=[(-7422.59, -18622.13)],
            actions=[
                RouteAction(name="Boss Fight Window", wait_ms=60_000),
                RouteAction(name="Chest", gadget_pos=(-7594.00, -18657.00)),
                RouteAction(name="Loot Chest", loot=True),
            ],
        ),
    ],
)
# Luxon - Mount Qinkai / Aspenwood Gate (Luxon title).
LUXON_ROUTE = Route(
    key="luxon",
    name="Luxon",
    icon="[1813] - Lightbringer.jpg",
    title_id=TitleID.Luxon,
    outpost_id=389,      # Aspenwood Gate (Luxon)
    explorable_id=200,   # Mount Qinkai
    exit_pos=(-5490.0, 13672.0),
    blessing_points=[Blessing((-8394.0, -9801.0))],
    kill_path=[
        (-13087.83, -9683.66), (-14952.93, -7771.10), (-16848.37, -9525.87),
        (-11624.00, -3465.98), (-13161.35, -1919.82), (-9122.62, -581.28),
        (-7091.64, 2400.57), (-2916.10, 8324.81), (-8317.40, 8299.81),
        (-10024.89, 2699.75), (-7352.91, 1323.47), (-6666.01, -4688.44),
        (-23.21, -9324.52), (5566.71, -3648.33), (6897.17, -196.10),
        (6243.11, -8762.36), (11648.28, -6957.10), (14615.63, -7808.74),
        (13236.78, -3757.25), (13283.18, 970.89), (10531.36, 8155.91),
        (5295.29, 6138.04), (2336.91, 1077.21), (-372.49, -2613.79),
    ],
)

# Kurzick - Drazach Thicket / Eternal Grove (Kurzick title).
KURZICK_ROUTE = Route(
    key="kurzick",
    name="Kurzick",
    icon="[1813] - Lightbringer.jpg",
    title_id=TitleID.Kurzick,
    outpost_id=222,      # Eternal Grove outpost
    explorable_id=195,   # Drazach Thicket
    exit_pos=(-7544.0, 14343.0),
    blessing_points=[Blessing((-5592.0, -16263.0))],
    kill_path=[
        (-9878.31, -14870.55), (-6024.71, -10824.51), (-4546.84, -9157.54),
        (-6683.80, -8867.51), (-7756.96, -9672.30), (-5651.87, -6857.37),
        (-6603.41, -5635.55), (-11036.84, -8096.66), (-12024.07, -8840.55),
        (-10875.07, -5594.80), (-10516.25, -2471.60), (-9792.65, -536.86),
        (-11308.45, 3273.95), (-12730.60, 5712.96), (-7237.03, -2142.75),
        (-7105.36, -2426.90), (-4554.99, 776.04), (-1223.03, 2129.13),
        (-1896.83, 5606.69), (-1813.93, -2020.71), (-5234.42, -5652.45),
    ],
)

# Sunspear - Arkjok Ward / Yohlon Haven (Sunspear title bounty loop).
SUNSPEAR_ROUTE = Route(
    key="sunspear",
    name="Sunspear",
    icon="[1816] - Sunspear Rebirth Signet.jpg",
    title_id=TitleID.Sunspear,
    outpost_id=381,      # Yohlon Haven
    explorable_id=380,   # Arkjok Ward
    exit_pos=(4603.0, 904.0),
    pre_path=[(-998.09, 1505.14)],
    bounty=True,
    bounty_pos=(-17223.00, -12543.00),
    bounty_dialog=0x85,
    kill_path=[
        (-18697.0, -12296.0),
        (-18557.0, -10503.0),
        (-17265.0, -15287.0),
        (-17158.0, -16655.0),
    ],
)

# Lightbringer - Mirror of Lyss / Gate of Pain (Lightbringer title bounty loop).
LIGHTBRINGER_ROUTE = Route(
    key="lightbringer",
    name="Lightbringer",
    icon="[1813] - Lightbringer.jpg",
    title_id=TitleID.Lightbringer,
    outpost_id=433,      # Gate of Pain
    explorable_id=419,   # Mirror of Lyss
    exit_pos=(-4779.0, -1726.0),
    bounty=True,
    bounty_pos=(19505.0, 11209.0),
    bounty_dialog=0x85,
    kill_path=[
        (15914.0, 10322.0),
        (12202.0, 8074.0),
        (13750.0, 5535.0),
        (13277.0, 3332.0),
        (11737.0, 1475.0),
        (10912.0, 3648.0),
        (20100.0, 7990.0),
        (19201.0, 733.0),
        (20273.0, -5210.0),
        (16293.0, -5574.0),
        (19066.0, -12837.0),
    ],
)

ALL_ROUTES: List[Route] = [
    VANGUARD_ROUTE,
    ASURAN_ROUTE,
    NORN_ROUTE,
    DELDRIMOR_ROUTE,
    LUXON_ROUTE,
    KURZICK_ROUTE,
    SUNSPEAR_ROUTE,
    LIGHTBRINGER_ROUTE,
]

def _route_by_key(key: str) -> Optional[Route]:
    for route in ALL_ROUTES:
        if route.key == key:
            return route
    return None


# The six rank-tracked reputation routes farmed by auto-rotate.
ROTATION_ROUTE_KEYS: Tuple[str, ...] = (
    "vanguard",
    "asuran",
    "norn",
    "deldrimor",
    "sunspear",
    "lightbringer",
)


def _reputation_routes() -> List[Route]:
    return [route for route in ALL_ROUTES if route.key in ROTATION_ROUTE_KEYS]

# Portal proximity gate
def _near_exit_gate(route: Route, radius: float = 1500.0) -> BehaviorTree:

    def _near_exit() -> BehaviorTree.NodeState:
        try:
            player_x, player_y = Player.GetXY()
        except Exception:
            return BehaviorTree.NodeState.FAILURE
        dx = player_x - route.exit_pos[0]
        dy = player_y - route.exit_pos[1]
        return (
            BehaviorTree.NodeState.SUCCESS
            if (dx * dx + dy * dy) <= radius * radius
            else BehaviorTree.NodeState.FAILURE
        )

    return BehaviorTree(
        BehaviorTree.ActionNode(name="Already Near Exit Portal?", action_fn=_near_exit)
    )

# Runtime state
botting_tree: Optional[BottingTree] = None
initialized: bool = False

selected_key: str = "vanguard"
_multi_account: bool = True  # Multibox build: always on.
_farm_all: bool = False
_target_rank: int = 5
_farm_to_max: bool = False
_active_farm_key: str = ""
_activate_conset: bool = True
_activate_pcons: bool = True


def _enabled_consumable_upkeeps() -> Tuple[int, ...]:
    enabled: List[int] = []
    if _activate_conset:
        enabled.extend(int(model_id) for model_id in CONSET_UPKEEPS)
    if _activate_pcons:
        enabled.extend(PCON_UPKEEPS)
    return tuple(dict.fromkeys(enabled))


# Faction point getters
def _kurzick_faction() -> int:
    try:
        return int(Player.GetKurzickData()[0] or 0)
    except Exception:
        return 0


def _luxon_faction() -> int:
    try:
        return int(Player.GetLuxonData()[0] or 0)
    except Exception:
        return 0


def _title_points(route: Route) -> int:
    try:
        title = Player.GetTitle(route.title_id)
        return int(title.current_points or 0) if title else 0
    except Exception:
        return 0


def _faction_points(route: Route) -> int:
    if route.key == "kurzick":
        return _kurzick_faction()
    if route.key == "luxon":
        return _luxon_faction()
    return _title_points(route)


def _account_points(account: object, route: Route) -> int:
    """Live faction/title points for one shared-memory account.

    Used by the account-aware goal check so a maxed leader does not stop the
    farm while the rest of the team is still below the target.
    """
    try:
        if route.key == "kurzick":
            faction = getattr(getattr(account, "FactionData", None), "Kurzick", None)
            return int(getattr(faction, "Current", 0) or 0)
        if route.key == "luxon":
            faction = getattr(getattr(account, "FactionData", None), "Luxon", None)
            return int(getattr(faction, "Current", 0) or 0)
        titles_data = getattr(account, "TitlesData", None)
        for title in getattr(titles_data, "Titles", []) or []:
            if int(getattr(title, "TitleID", 0) or 0) == int(route.title_id):
                return int(getattr(title, "CurrentPoints", 0) or 0)
        return 0
    except Exception:
        return 0


def _all_accounts_at_goal(route: Route, threshold: int) -> bool:
    """True only when EVERY active account is at or above the route's goal."""
    accounts = _all_active_accounts()
    if not accounts:
        return False
    return all(_account_points(account, route) >= int(threshold) for account in accounts)


def _account_rank(account: object, route: Route) -> int:
    """Tier index (1-based) of a single account's route title, 0 when unranked."""
    points = _account_points(account, route)
    rank = 0
    for tier in TITLE_TIERS.get(int(route.title_id), []):
        if points >= int(tier.required):
            rank = int(tier.tier)
    return rank


# NPC approach helper. Bare BT.Move treats the destination NPC as a wall
# (ignore_destination_obstacles defaults False), so the approach orbits.
# Arming destination-ignore drives straight at the NPC for the dialog.
def _move_to_npc(pos: Tuple[float, float]) -> BehaviorTree:
    return BT.Move(
        pos,
        tolerance=120.0,
        log=True,
        ignore_destination_obstacles=True,
        destination_obstacle_ignore_distance=2500.0,
        ignore_destination_npcs=True,
        ignore_destination_gadgets=True,
    )


def AggressiveEnv() -> Sequence[BehaviorTree]:
    return [
        ensure_botting_tree().Config.Aggressive(multi_account=_multi_account, auto_loot=True),
    ]


# Route-specific blessing/bounty effect IDs. Source of truth is the canonical
# table in Sources/aC_Scripts/aC_api/Verify_Blessing.py (already consumed by
# the legacy blessed helpers and PyQuishAI). A grab is confirmed once ANY of
# the route's effect IDs is active on the leader.
_BLESSING_IDS_BY_ROUTE: Dict[str, Tuple[int, ...]] = {
    "vanguard": tuple(Blessings.Vanguard_Patrol.ids),
    "asuran": tuple(Blessings.Asuran_Bodyguard.ids),
    "norn": tuple(Blessings.Norn_Hunting_Party.ids),
    "deldrimor": tuple(Blessings.Dwarven_Raider.ids),
    "kurzick": tuple(Blessings.Blessing_of_the_Kurzicks.ids),
    "luxon": tuple(Blessings.Blessing_of_the_Luxons.ids),
    "sunspear": tuple(
        effect_id
        for member in Blessings
        if member.name
        in {
            "Corsair_Bounty",
            "Giant_Hunt",
            "Heket_Hunt",
            "Kournan_Bounty",
            "Mandragor_Hunt",
            "Minotaur_Hunt",
            "Monster_Hunt",
            "Plant_Hunt",
            "Skale_Hunt",
            "Skree_Battle",
            "Undead_Hunt",
            "Insect_Hunt",
        }
        for effect_id in member.ids
    ),
    "lightbringer": tuple(
        effect_id
        for member in Blessings
        if member.name
        in {
            "Anguish_Hunt",
            "Demon_Hunt",
            "Dhuum_Battle",
            "Elemental_Hunt",
            "Margonite_Battle",
            "Menzies_Battle",
            "Monolith_Hunt",
            "Titan_Hunt",
        }
        for effect_id in member.ids
    ),
}


def _confirm_effect(
    candidate_ids: Tuple[int, ...],
    name: str,
    *,
    timeout_ms: int = 10000,
    throttle_interval_ms: int = 250,
) -> BehaviorTree:
    """Succeed once any of the route's blessing/bounty effects is on the leader."""

    def _condition(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        try:
            me = int(Player.GetAgentID() or 0)
            if me > 0:
                for effect_id in candidate_ids:
                    if GLOBAL_CACHE.Effects.EffectExists(me, effect_id):
                        return BehaviorTree.NodeState.SUCCESS
        except Exception:
            pass
        return BehaviorTree.NodeState.RUNNING

    return BehaviorTree(
        BehaviorTree.WaitUntilNode(
            name=name,
            condition_fn=_condition,
            throttle_interval_ms=max(1, int(throttle_interval_ms)),
            timeout_ms=max(1, int(timeout_ms)),
        )
    )


def _confirmed_interaction(
    interaction: BehaviorTree,
    *,
    confirm_ids: Tuple[int, ...],
    name: str,
    attempts: int = 3,
    settle_ms: int = 1500,
    confirm_timeout_ms: int = 10000,
) -> BehaviorTree:
    """Run an interaction and confirm the blessing/bounty actually landed.

    Headless Hero AI stays fully enabled the whole time -- toggling it off
    breaks the multibox movement/skill loop (confirmed live), so an interrupted
    dialog is caught by the route's effect check instead and the interaction is
    retried. Re-arming Hero AI up front is a one-way safety: it is a no-op when
    headless is already enabled, and the multibox widget broadcast is
    deduplicated, so no alt Hero AI churn.

    Multibox note: this confirms only the LEADER's effect. Followers receive
    the blessing/bounty through the SendDialogToTarget / SendDialog fan-outs,
    whose receivers are wired in Widgets/System/Messaging.py. (The older
    TakeBlessing multibox path used SharedCommandType.GetBlessing, whose
    ProcessMessages case was a no-op -- elevated to the messaging-layer
    owners, not fixed here.)
    """
    def _already_confirmed(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        # Idempotency gate: if the route's effect is already on the leader
        # (previous attempt landed, or an earlier run left it active), skip the
        # interaction -- one-shot shrines never re-open their dialog, so
        # re-clicking would only spin until the retry timeout (seen live).
        try:
            me = int(Player.GetAgentID() or 0)
            if me > 0:
                for effect_id in confirm_ids:
                    if GLOBAL_CACHE.Effects.EffectExists(me, effect_id):
                        return BehaviorTree.NodeState.SUCCESS
        except Exception:
            pass
        return BehaviorTree.NodeState.FAILURE

    interaction_children: List[BehaviorTree] = [interaction, BT.Wait(settle_ms)]
    if confirm_ids:
        interaction_children.append(
            _confirm_effect(
                confirm_ids,
                name=f"{name} Confirmation",
                timeout_ms=confirm_timeout_ms,
            )
        )

    attempt_children: List[BehaviorTree] = [
        BottingTree.EnableHeroAITree(reset_runtime=True),
        BT.Wait(250),
        BT.Selector(
            name=f"{name} Already Confirmed?",
            children=[
                BehaviorTree(
                    BehaviorTree.ActionNode(
                        name=f"{name} Effect Already Present",
                        action_fn=_already_confirmed,
                    )
                ),
                BT.Sequence(name=f"{name} Interact", children=interaction_children),
            ],
        ),
    ]

    attempt = BT.Sequence(name=f"{name} Attempt", children=attempt_children)
    return BehaviorTree(
        BehaviorTree.RepeaterUntilSuccessNode(
            child=attempt.root,
            timeout_ms=max(
                1000,
                int(attempts) * (int(settle_ms) + int(confirm_timeout_ms)) + 60_000,
            ),
            name=f"{name} Confirm Retry",
        )
    )


# Hero team setup



def _multibox_party_already_formed() -> bool:
    from Py4GWCoreLib.routines_src.behaviourtrees_src.shared import (
        _account_emails_not_on_same_map_as_local,
        _account_is_in_local_party,
    )

    try:
        if _account_emails_not_on_same_map_as_local():
            return False
        sender_email = str(Player.GetAccountEmail() or "")
        if not sender_email:
            return False
        if GLOBAL_CACHE.ShMem.GetAccountDataFromEmail(sender_email) is None:
            return False
        for account in GLOBAL_CACHE.ShMem.GetAllAccountData() or []:
            receiver_email = str(getattr(account, "AccountEmail", "") or "")
            if not receiver_email or receiver_email == sender_email:
                continue
            if not _account_is_in_local_party(account):
                return False
    except Exception:
        return False
    return True


def _multibox_party_setup(timeout_ms: int = 60_000) -> BehaviorTree:
    def _party_intact(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        return (
            BehaviorTree.NodeState.SUCCESS
            if _multibox_party_already_formed()
            else BehaviorTree.NodeState.FAILURE
        )

    return BT.Selector(
        name="MultiboxPartySetup",
        children=[
            BT.Sequence(
                name="PartyAlreadyFormed?",
                children=[
                    BehaviorTree(
                        BehaviorTree.ActionNode(
                            name="MultiboxPartyAlreadyFormed?",
                            action_fn=_party_intact,
                        )
                    ),
                    BT.LogMessage(
                        "Multibox party already formed - skipping disband/reinvite",
                        module_name=MODULE_NAME,
                    ),
                ],
            ),
            BT.CreateParty(multibox_invite=True, timeout_ms=timeout_ms),
        ],
    )


# ---------------------------------------------------------------------------
# Multibox resign helpers (ported from Shards Of Orr / Sulfurous Wastes)
# ---------------------------------------------------------------------------


def _all_active_accounts() -> list:
    """Return a de-duplicated list of all active shared-memory accounts."""
    try:
        accounts = GLOBAL_CACHE.ShMem.GetAllAccountData(sort_results=False)
    except TypeError:
        accounts = GLOBAL_CACHE.ShMem.GetAllAccountData()
    except Exception:
        accounts = []

    unique: list = []
    seen: set = set()
    for account in accounts or []:
        email = str(getattr(account, "AccountEmail", "") or "").strip()
        if not email or email in seen:
            continue
        seen.add(email)
        unique.append(account)
    return unique


def _account_map_id(account: object) -> int:
    """Resolve the live map ID for a shared-memory account entry."""
    agent_data = getattr(account, "AgentData", None)
    map_data = getattr(agent_data, "Map", None)
    return int(getattr(map_data, "MapID", 0) or 0)


def _all_accounts_on_map(map_id: int) -> bool:
    """True when every active account is on the given map."""
    accounts = _all_active_accounts()
    return bool(accounts) and all(_account_map_id(a) == int(map_id) for a in accounts)


def _wait_for_all_accounts_on_map(
    map_id: int,
    *,
    name: str,
    timeout_ms: int = 60000,
) -> BehaviorTree:
    """BehaviorTree node that blocks until every active account is on map_id."""

    def _check(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        if _all_accounts_on_map(map_id):
            return BehaviorTree.NodeState.SUCCESS
        return BehaviorTree.NodeState.RUNNING

    return BehaviorTree(
        BehaviorTree.WaitUntilNode(
            name=name,
            condition_fn=_check,
            throttle_interval_ms=500,
            timeout_ms=timeout_ms,
        )
    )


def _resign_node(route: Route) -> BehaviorTree:
    """Resign-only multibox return: the team ALWAYS goes home by resigning.

    This build must never map-travel the party back to the outpost, so the
    Shards Of Orr / Sulfurous Wastes TravelToMap fallback is intentionally
    absent. The resign fan-out reaches every shared-memory account (a receiver
    in an outpost ignores /resign harmlessly), so no "leader is in the
    explorable" gate is needed either: re-issue the resign until every account
    is back at the outpost or RESIGN_RETRY_TIMEOUT_MS expires.
    """
    resign_attempt = BT.Sequence(
        name=f"Resign {route.name} Party To Outpost",
        children=[
            BT.Resign(
                wait_for_map_load=True,
                target_map_id=route.outpost_id,
                multi_account=True,
                timeout_ms=60000,
                log=True,
            ),
            _wait_for_all_accounts_on_map(
                route.outpost_id,
                name=f"Wait For {route.name} Party Return To Outpost",
            ),
        ],
    )

    return BT.Selector(
        name=f"Ensure Every Account Is At The {route.name} Outpost",
        children=[
            BehaviorTree(
                BehaviorTree.ConditionNode(
                    name=f"Every Account Already At {route.name} Outpost",
                    condition_fn=lambda _node, mid=route.outpost_id: _all_accounts_on_map(mid),
                )
            ),
            BehaviorTree(
                BehaviorTree.RepeaterUntilSuccessNode(
                    child=resign_attempt.root,
                    timeout_ms=RESIGN_RETRY_TIMEOUT_MS,
                    name=f"{route.name} Resign Retry",
                )
            ),
        ],
    )


def _multibox_resign_home(route: Route) -> BehaviorTree:
    """Resign every shared-memory account home before entering the explorable.

    A multibox party must return by resigning (which reaches every account),
    never by map-traveling (which only moves the leader). Issued at farm
    startup (ahead of the one-time outpost travel) and at the top of every run
    loop iteration: a no-op for accounts already at the outpost (receivers in
    an outpost ignore /resign harmlessly), a rescue for any still inside the
    explorable -- e.g. a Run Retry restarting mid-route.
    """
    return BT.Resign(
        multi_account=True,
        wait_for_map_load=True,
        target_map_id=route.outpost_id,
        timeout_ms=60000,
        log=True,
    )


def _multibox_party_wipe_recovery() -> BehaviorTree:
    """Own party-wipe recovery: on a true defeat, resign the WHOLE team home.

    The stock BottingTree recovery service only calls ReturnToOutport() on the
    local (leader) client, which leaves every alt account defeated at the wipe
    spot. The leader then restarts the farm step and the leading BT.Travel can
    never bring a split party back - only a full team resign can. This service
    dispatches the same SharedCommandType.Resign fan-out that bot.Multibox.
    ResignParty uses to every shared-memory account, waits until every account
    is back at the route's outpost, and only then requests a planner step
    restart.

    ANY party wipe or unrecoverable leader death resigns the whole team back to
    the outpost - these are short farms, so a retry always beats salvaging a
    split party. Res-shrine respawns are NOT used to restart in place: that
    leaves the leader at the shrine while the alts are elsewhere, and the next
    BT.Travel then runs without the party. The service also no-ops while
    `party_wipe_recovery_suppressed` is on the shared blackboard, which
    BT.Resign sets around the normal end-of-run resign so a legitimate resign
    is never mistaken for a wipe.
    """
    state: Dict[str, Any] = {
        "active": False,
        "mode": "",
        "step_name": "",
        "recovery_started_ms": 0.0,
        "last_resign_ms": 0.0,
        "player_was_dead": False,
        "player_dead_pos": None,
        "leader_dead_since_ms": 0.0,
        "suppressed_since_ms": 0.0,
        "last_diag_ms": 0.0,
    }
    resign_interval_ms = 2000.0

    def _log(message: str, message_type=PySystem.Console.MessageType.Warning) -> None:
        PySystem.Console.Log(MODULE_NAME, message, message_type)

    def _resolve_step(node: BehaviorTree.Node) -> str:
        named = {str(name) for name in (node.blackboard.get("named_planner_step_names", []) or [])}
        current = str(node.blackboard.get("current_step_name", "") or "")
        last_active = str(node.blackboard.get("last_active_planner_step_name", "") or "")
        if current and (not named or current in named):
            return current
        if last_active and (not named or last_active in named):
            return last_active
        for name in node.blackboard.get("named_planner_step_names", []) or []:
            if str(name):
                return str(name)
        return ""

    def _detect_revive_teleport() -> bool:
        from Py4GWCoreLib.Agent import Agent
        from Py4GWCoreLib.enums_src.GameData_enums import Range
        from Py4GWCoreLib.py4gwcorelib_src.Utils import Utils

        player_id = Player.GetAgentID()
        if not Agent.IsValid(player_id):
            return False
        current_pos = Agent.GetXY(player_id)
        is_dead = bool(Agent.IsDead(player_id))
        if is_dead:
            if not state["player_was_dead"]:
                state["player_was_dead"] = True
                state["player_dead_pos"] = current_pos
                return False
            death_pos = state["player_dead_pos"]
            if death_pos and Utils.Distance(death_pos, current_pos) > Range.Spellcast.value:
                state["player_was_dead"] = False
                state["player_dead_pos"] = None
                return True
            return False
        if not state["player_was_dead"]:
            return False
        state["player_was_dead"] = False
        death_pos = state["player_dead_pos"]
        state["player_dead_pos"] = None
        return bool(death_pos and Utils.Distance(death_pos, current_pos) > Range.Spellcast.value)

    def _team_is_at_outpost(route: Optional[Route]) -> bool:
        if not route or not route.outpost_id:
            return False
        return _all_accounts_on_map(int(route.outpost_id))

    def _leader_is_at_outpost() -> bool:
        return bool(Map.IsMapReady() and Map.IsOutpost() and GLOBAL_CACHE.Party.IsPartyLoaded())

    def _begin(node: BehaviorTree.Node, mode: str) -> None:
        state["active"] = True
        state["mode"] = mode
        state["step_name"] = _resolve_step(node)
        state["recovery_started_ms"] = time.monotonic() * 1000.0
        state["last_resign_ms"] = 0.0
        node.blackboard["party_wipe_recovery_active"] = True
        node.blackboard["party_wipe_recovery_mode"] = mode
        node.blackboard["party_wipe_recovery_step_name"] = state["step_name"]
        if mode == "leader_dead":
            _log("Party leader has been dead past the revival grace period; resigning the whole team to the outpost.")
        else:
            _log("Party wipe detected; resigning the whole team to the outpost.")

    def _reset(node: BehaviorTree.Node) -> None:
        state["active"] = False
        state["mode"] = ""
        state["step_name"] = ""
        state["recovery_started_ms"] = 0.0
        state["last_resign_ms"] = 0.0
        state["player_was_dead"] = False
        state["player_dead_pos"] = None
        state["leader_dead_since_ms"] = 0.0
        node.blackboard["party_wipe_recovery_active"] = False
        node.blackboard["party_wipe_recovery_mode"] = ""
        node.blackboard["party_wipe_recovery_step_name"] = ""

    def _request_restart(node: BehaviorTree.Node) -> bool:
        step_name = state["step_name"] or _resolve_step(node)
        if not step_name:
            _log("Recovery finished, but no planner step could be resolved.")
            return False
        node.blackboard["restart_step_name_request"] = step_name
        node.blackboard["PLANNER_STATUS"] = f"PLANNER: Restarting {step_name}"
        return True

    def _dispatch_full_resign() -> None:
        sender_email = str(Player.GetAccountEmail() or "")
        if not sender_email:
            return
        for account in GLOBAL_CACHE.ShMem.GetAllAccountData():
            receiver_email = str(getattr(account, "AccountEmail", "") or "")
            if not receiver_email:
                continue
            GLOBAL_CACHE.ShMem.SendMessage(
                sender_email,
                receiver_email,
                SharedCommandType.Resign,
                (0.0, 0.0, 0.0, 0.0),
            )
        _log("Party defeat: dispatching full multibox resign back to the outpost.")

    def _tick(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        route = _route_by_key(_active_farm_key) or _route_by_key(selected_key)
        now = time.monotonic() * 1000.0

        suppressed = bool(node.blackboard.get("party_wipe_recovery_suppressed", False))
        if suppressed:
            if state["suppressed_since_ms"] == 0.0:
                state["suppressed_since_ms"] = now
            if now - state["suppressed_since_ms"] >= RESIGN_SUPPRESS_STALE_MS:
                # A failed BT.Resign leaks this flag: the unsuppress child never
                # runs when the resign sequence fails, which would silence wipe
                # recovery forever. Clear it once the leak is obvious.
                _log("party_wipe_recovery_suppressed stuck for 2+ minutes; clearing it.")
                node.blackboard["party_wipe_recovery_suppressed"] = False
                state["suppressed_since_ms"] = 0.0
                suppressed = False
            else:
                _reset(node)
                return BehaviorTree.NodeState.RUNNING
        else:
            state["suppressed_since_ms"] = 0.0

        # Detect the wipe flags BEFORE any map-readiness gate: a full party
        # wipe can leave Map.IsMapReady() False (defeat/transition state), so
        # gating detection on it here would silently strand the dead team -
        # the exact failure we must avoid. The stock core
        # PartyWipeRecoveryServiceTree likewise computes wiped/defeated first.
        from Py4GWCoreLib.Agent import Agent
        from Py4GWCoreLib.Routines import Routines

        player_id = Player.GetAgentID()
        leader_dead = bool(Agent.IsValid(player_id) and Agent.IsDead(player_id))
        party_wiped = bool(Routines.Checks.Party.IsPartyWiped())
        party_defeated = bool(GLOBAL_CACHE.Party.IsPartyDefeated())
        revived_at_shrine = _detect_revive_teleport()

        if now - state["last_diag_ms"] >= DIAGNOSTIC_HEARTBEAT_MS:
            state["last_diag_ms"] = now
            _log(
                f"wipe-recovery heartbeat: map={Map.GetMapID()} wiped={party_wiped} "
                f"defeated={party_defeated} leader_dead={leader_dead} suppressed={suppressed}",
                PySystem.Console.MessageType.Info,
            )

        if not state["active"]:
            if not leader_dead:
                state["leader_dead_since_ms"] = 0.0
            elif state["leader_dead_since_ms"] == 0.0:
                state["leader_dead_since_ms"] = now

            leader_stuck = bool(
                leader_dead
                and not party_wiped
                and not party_defeated
                and state["leader_dead_since_ms"] > 0.0
                and now - state["leader_dead_since_ms"] >= LEADER_DEATH_GRACE_MS
            )

            # ANY party wipe / defeat / res-shrine respawn / unrecoverable leader
            # death resigns the whole team back to the outpost. These are short
            # farms: a retry always beats salvaging a split party.
            if not (party_wiped or party_defeated or revived_at_shrine or leader_stuck):
                node.blackboard["party_wipe_recovery_active"] = False
                return BehaviorTree.NodeState.RUNNING

            _begin(node, "leader_dead" if leader_stuck else "defeated")
            return BehaviorTree.NodeState.RUNNING

        node.blackboard["party_wipe_recovery_active"] = True
        node.blackboard["party_wipe_recovery_mode"] = state["mode"]
        node.blackboard["party_wipe_recovery_step_name"] = state["step_name"]

        # Every active recovery mode (defeated / leader_dead) resigns the whole
        # team back to the outpost, re-issuing until every account is confirmed
        # there. Never restart while alts are still scattered - a fresh
        # BT.Travel would just repeat the split-party bug.
        exhausted = now - state["recovery_started_ms"] >= RESIGN_RETRY_TIMEOUT_MS
        team_home = _team_is_at_outpost(route)
        leader_home_fallback = route is None and _leader_is_at_outpost()

        if team_home or leader_home_fallback or exhausted:
            restarted = _request_restart(node)
            _reset(node)
            return BehaviorTree.NodeState.SUCCESS if restarted else BehaviorTree.NodeState.FAILURE

        if now - state["last_resign_ms"] >= resign_interval_ms:
            _dispatch_full_resign()
            state["last_resign_ms"] = now

        return BehaviorTree.NodeState.RUNNING

    return BehaviorTree(
        BehaviorTree.ActionNode(
            name="MultiboxPartyWipeRecovery",
            action_fn=_tick,
            aftercast_ms=0,
        )
    )


def _party_setup(route: Route) -> List[BehaviorTree]:
    # Multibox build: summon and invite every shared-memory account. The solo
    # hero-team flow lives in Reputation Farmer BT.py.
    return [_multibox_party_setup()]


def _route_action_nodes(route: Route, segment: RouteSegment) -> List[BehaviorTree]:
    """Build the BT nodes for a segment's post-path dungeon actions.

    One node per RouteAction, in list order. Gadget interactions target the
    nearest gadget to the given XY and are leader-only; HeroAI suspension is
    disabled on purpose -- the suspend/restore toggle is exactly what this
    module's header documents as breaking the multibox movement/skill loop.
    """
    nodes: List[BehaviorTree] = []
    for action in segment.actions:
        label = action.name or f"{route.name} Segment Action"
        if action.wait_out_of_combat:
            nodes.append(BT.WaitUntilOutOfCombat())
        if action.loot:
            nodes.append(BT.LootItems())
        if action.gadget_pos is not None:
            nodes.append(
                BT.Sequence(
                    name=f"{route.name} {label}",
                    children=[
                        BT.MoveAndInteractWithGadget(
                            pos=action.gadget_pos,
                            search_distance=1500.0,
                            suspend_hero_ai=False,
                            log=True,
                        ),
                    ],
                )
            )
        if action.wait_ms > 0:
            nodes.append(BT.Wait(int(action.wait_ms)))
    return nodes


def _killing_loop(route: Route) -> BehaviorTree:
    """The vanquish-style farming loop for one faction run.

    Mirrors the Nightfall Leveler / VQFarm convention: cross into the
    explorable map from the outpost, then either run per-leg route segments
    (blessing + fight leg each) or collect the route's blessings and run its
    single kill path, wait out of combat, then resign back to the outpost.
    The outpost travel is NOT part of this loop: the farm sequence performs it
    once at startup, and every run END resigns the team back to the outpost.
    """
    children: List[BehaviorTree] = [
        # No per-run map travel: the farm sequence travels to the outpost once
        # at startup, and each run's end-of-run _resign_node returns the team
        # there. This home-first resign is the retry rescue, not a travel: a
        # no-op for a team already at the outpost, and the gather-home for a
        # Run Retry that restarts while the team is still inside the
        # explorable.
        _multibox_resign_home(route),
        # Followers may still be trickling in from the end-of-run / retry
        # resign; this pause lets the team regroup before party setup.
        BT.Wait(10_000),
        *_party_setup(route),
    ]

    if route.key in ("kurzick", "luxon"):
        children.append(BT.EqualizeGold(target_gold=BLESSING_GOLD, log=True))
    if route.entry_dialog_id:
        children.append(_move_to_npc(route.exit_pos))
        children.append(BT.DialogAtXY(route.exit_pos, route.entry_dialog_id, target_distance=300.0, log=True))
        children.append(BT.WaitForMapLoad(map_id=route.explorable_id))
    elif route.exit_by_name:
        children.append(
            BT.MoveAndExitMap(route.exit_pos, target_map_name=str(route.explorable_id))
        )
    else:
        children.append(
            BT.MoveAndExitMap(route.exit_pos, target_map_id=route.explorable_id)
        )

    children.extend(AggressiveEnv())

    def _take_blessing(blessing: Blessing) -> None:
        if route.key in ("kurzick", "luxon"):
            # Faction path fans out through InteractTargetAndSendDialog ->
            # SendDialogToTarget, whose receiver dispatch is wired.
            blessing_tree = BT.TakeBlessing(
                blessing.pos,
                faction=route.key,
                multi_account=_multi_account,
            )
        else:
            # Non-faction path: BT.TakeBlessing's multibox fan-out uses
            # SharedCommandType.GetBlessing, whose ProcessMessages case is a
            # no-op in Messaging.py (elevated), so followers never received the
            # blessing. Mirror its local auto-dialog semantics through the
            # fan-out commands whose receivers ARE wired:
            # TakeDialogWithTarget + SendDialog.
            blessing_tree = BT.Sequence(
                name=f"{route.name} Shrine Approach",
                children=[
                    BT.Move(blessing.pos, tolerance=150.0, log=True),
                    BT.TargetNearestAndAutoDialog(
                        blessing.pos,
                        buttons=0,
                        multi_account=True,
                        log=True,
                    ),
                ],
            )
        children.append(
            _confirmed_interaction(
                blessing_tree,
                confirm_ids=(
                    _BLESSING_IDS_BY_ROUTE.get(route.key, ())
                    if blessing.confirm
                    else ()
                ),
                name=f"{route.name} Blessing",
            )
        )


    if route.segments:
        for index, segment in enumerate(route.segments, start=1):
            if segment.blessing is not None:
                _take_blessing(segment.blessing)
            if segment.path:
                children.append(
                    BT.VanquishNode(
                        steps=list(segment.path),
                        name=segment.name or f"{route.name} Leg {index}",
                        flag_heroes_to_waypoint=False,
                    )
                )
            children.extend(_route_action_nodes(route, segment))
    else:
        for blessing in route.blessing_points:
            _take_blessing(blessing)

        if route.kill_path:
            children.append(
                BT.VanquishNode(
                    steps=list(route.kill_path),
                    name=f"{route.name} Kill Path",
                    flag_heroes_to_waypoint=False,
                )
            )

    children.append(BT.WaitUntilOutOfCombat())
    children.append(_resign_node(route))

    return BT.Sequence(name=f"{route.name} VQ Run", children=children)


def _bounty_loop(route: Route) -> BehaviorTree:
    """One Nightfall bounty run (Sunspear / Lightbringer).

    Mirrors the legacy bounty bots: cross into the explorable map from the
    outpost, accept the bounty from the NPC via dialog, run the kill path,
    then wait out of combat and resign back to the outpost. The outpost
    travel is NOT part of this loop: the farm sequence performs it once at
    startup, and every run END resigns the team back to the outpost.
    """
    if route.bounty_pos is None:
        return BT.LogMessage(
            f"{route.name} route has no bounty position configured.",
            module_name=MODULE_NAME,
        )

    children: List[BehaviorTree] = [
        # No per-run map travel: the farm sequence travels to the outpost once
        # at startup, and each run's end-of-run _resign_node returns the team
        # there. This home-first resign is the retry rescue, not a travel: a
        # no-op for a team already at the outpost, and the gather-home for a
        # Run Retry that restarts while the team is still inside the
        # explorable.
        _multibox_resign_home(route),
        # Followers may still be trickling in from the end-of-run / retry
        # resign; this pause lets the team regroup before party setup.
        BT.Wait(10_000),
        *_party_setup(route),
    ]
    for point in route.pre_path:
        children.append(
            BehaviorTree(
                BehaviorTree.SelectorNode(
                    name="Pre-Path Needed?",
                    children=[
                        _near_exit_gate(route),
                        BT.Move(point, tolerance=150.0, log=True),
                    ],
                )
            )
        )
    children.append(BT.MoveAndExitMap(route.exit_pos, target_map_id=route.explorable_id))
    children.extend(AggressiveEnv())
    children.append(
        _confirmed_interaction(
            BT.Sequence(
                name=f"Accept {route.name} Bounty",
                children=[
                    _move_to_npc(route.bounty_pos),
                    BT.DialogAtXY(
                        route.bounty_pos,
                        route.bounty_dialog,
                        target_distance=300.0,
                        log=True,
                        multi_account=_multi_account,
                    ),
                ],
            ),
            confirm_ids=_BLESSING_IDS_BY_ROUTE.get(route.key, ()),
            name=f"{route.name} Bounty Accept",
        )
    )

    if route.kill_path:
        children.append(
            BT.VanquishNode(
                steps=list(route.kill_path),
                name=f"{route.name} Bounty Kill Path",
                flag_heroes_to_waypoint=False,
                log=True,
            )
        )

    children.append(BT.WaitUntilOutOfCombat())
    children.append(_resign_node(route))

    return BT.Sequence(name=f"{route.name} Bounty Run", children=children)
# ---------------------------------------------------------------------------
# Planner step builders
# ---------------------------------------------------------------------------
def _current_rank(route: Route) -> int:
    """Current tier index (1-based) of a route's title, 0 when unranked.

    Uses the same points scan as the live UI readout, so the goal check and
    the displayed tier always agree.
    """
    points = _faction_points(route)
    rank = 0
    for tier in TITLE_TIERS.get(int(route.title_id), []):
        if points >= tier.required:
            rank = int(tier.tier)
    return rank


def _route_goal_rank(route: Route) -> Optional[int]:
    """Target tier for a rank-tracked route, or None when no tier data exists.

    `_farm_to_max` pushes to the title's top tier; otherwise the route farms
    to `_target_rank` (default 5, where faction skills / most unlocks cap).
    """
    tiers = TITLE_TIERS.get(int(route.title_id), [])
    if not tiers:
        return None
    if _farm_to_max:
        return int(max(tier.tier for tier in tiers))
    return _target_rank


def _goal_threshold(route: Route) -> Optional[int]:
    """Point threshold at which the route is done.

    Kurzick/Luxon stop at the faction donate cap (FACTION_GOAL) — the Canthan
    donation loop, which has no reputation-rank target. All rank-tracked routes
    stop at the configured target: `_farm_to_max` pushes to the title's top
    tier, otherwise `_target_rank` (default 5). With no tier data the route
    never completes on points alone. The threshold is read live at tick time,
    so changing `_farm_to_max` / `_target_rank` needs no planner rebuild.
    """
    if route.key in ("kurzick", "luxon"):
        return FACTION_GOAL
    goal_rank = _route_goal_rank(route)
    if goal_rank is None:
        return None
    for tier in TITLE_TIERS.get(int(route.title_id), []):
        if int(tier.tier) == goal_rank:
            return int(tier.required)
    return None


def _build_farm_sequence(route: Route) -> BehaviorTree:
    goal_state = {"decision": ""}

    def _log_goal_decision(decision: str, detail: str) -> None:
        # Edge-triggered: the goal check runs every tick, so only log changes.
        if goal_state["decision"] == decision:
            return
        goal_state["decision"] = decision
        PySystem.Console.Log(MODULE_NAME, f"{route.name}: {detail}", PySystem.Console.MessageType.Info)

    def _goal_reached() -> BehaviorTree.NodeState:
        global _active_farm_key
        threshold = _goal_threshold(route)
        points = _faction_points(route)
        if threshold is None:
            _log_goal_decision("skip", "no goal threshold configured; farming skipped.")
            return BehaviorTree.NodeState.FAILURE
        if _all_accounts_at_goal(route, threshold):
            _log_goal_decision("skip", f"every account at goal {threshold} (leader {points}); run skipped.")
            return BehaviorTree.NodeState.SUCCESS
        _active_farm_key = route.key
        below = sum(1 for account in _all_active_accounts() if _account_points(account, route) < int(threshold))
        _log_goal_decision("farm", f"leader {points}/{threshold}; {below} account(s) still below goal.")
        return BehaviorTree.NodeState.FAILURE

    def _one_run() -> BehaviorTree:
        run_loop = _bounty_loop(route) if route.bounty else _killing_loop(route)
        retried_run = BehaviorTree(
            BehaviorTree.RepeaterUntilSuccessNode(
                child=run_loop.root,
                timeout_ms=RUN_RETRY_TIMEOUT_MS,
                name=f"{route.name} Run Retry",
            )
        )
        return BehaviorTree(
            BehaviorTree.SelectorNode(
                name=f"{route.name} Run Or Skip",
                children=[
                    BehaviorTree(BehaviorTree.ActionNode(name="Goal Reached?", action_fn=_goal_reached)),
                    retried_run,
                ],
            )
        )

    def _farm_startup() -> BehaviorTree:
        """One-time startup ahead of the run loop: form the team, then travel.

        The map travel to the route's outpost happens HERE ONLY -- once,
        before the farm loop begins -- never per run, and it carries the WHOLE party (formed here first,
        ahead of the travel). Every run END still resigns the team back to
        the outpost via _resign_node, so each loop iteration starts from the
        outpost with no travel. The goal check gates the startup: a route
        already at its target skips everything, exactly as its runs are
        skipped.
        """
        return BehaviorTree(
            BehaviorTree.SelectorNode(
                name=f"{route.name} Farm Startup",
                children=[
                    BehaviorTree(
                        BehaviorTree.ActionNode(
                            name="Goal Reached?",
                            action_fn=_goal_reached,
                        )
                    ),
                    BT.Sequence(
                        name=f"{route.name} Startup Travel",
                        children=[
                            # Gather a scattered team home first. No map wait:
                            # at first start the leader is NOT yet at the route
                            # outpost (resigning from an outpost is a no-op and
                            # never moves anyone), so waiting here for it would
                            # only time out. The multibox fan-out itself still
                            # completes: receivers ack the resign, and anyone
                            # still inside an explorable does return home.
                            BT.Resign(
                                multi_account=True,
                                wait_for_map_load=False,
                                timeout_ms=60000,
                                log=True,
                            ),
                            # Form the multibox party BEFORE the travel so the
                            # party travel carries every account to the route
                            # outpost together, instead of the leader traveling
                            # alone and run 1 having to summon stragglers
                            # cross-map.
                            *_party_setup(route),
                            BT.Travel(
                                target_map_id=route.outpost_id,
                                random_travel=True,
                                hard_mode=True,
                            ),
                            BT.Wait(10_000),
                        ],
                    ),
                ],
            )
        )

    return BT.Sequence(
        name=f"Farm {route.name}",
        children=[
            _farm_startup(),
            BT.Repeater(name=f"Farm {route.name}", repeat_count=VQ_MAX_RUNS, children=[_one_run()]),
        ],
    )


def FarmFaction() -> BehaviorTree:
    route = _route_by_key(selected_key)
    if route is None:
        return BT.LogMessage(f"Unknown faction: {selected_key}", module_name=MODULE_NAME)
    return _build_farm_sequence(route)


def FarmAll() -> BehaviorTree:
    children: List[BehaviorTree] = []
    for route in _reputation_routes():
        threshold = _goal_threshold(route)
        if threshold is None:
            continue
        children.append(_build_farm_sequence(route))
    if not children:
        return BT.LogMessage(
            "All reputation routes have reached the target rank.",
            module_name=MODULE_NAME,
        )
    return BT.Sequence(name="Farm All Reputation", children=children)


HOUSE_ZU_HELZER = 77    # Kurzick donation outpost
CAVALON = 193           # Luxon donation outpost


def DonateFaction() -> BehaviorTree:
    route = _route_by_key(selected_key)
    if route is None:
        return BT.LogMessage(f"Unknown faction: {selected_key}", module_name=MODULE_NAME)
    if route.key not in ("kurzick", "luxon"):
        return BT.Succeeder()

    donation_map = {"kurzick": "kurzick", "luxon": "luxon"}
    travel_map = {"kurzick": HOUSE_ZU_HELZER, "luxon": CAVALON}
    return BT.DonateFaction(
        faction=donation_map[route.key],
        threshold=FACTION_GOAL,
        travel_map_id=travel_map[route.key],
        multi_account=_multi_account,
        log=True,
    )


def get_execution_steps() -> List[Tuple[str, Callable[[], BehaviorTree]]]:
    if _farm_all:
        return [("Farm All Reputation", FarmAll)]
    steps: List[Tuple[str, Callable[[], BehaviorTree]]] = [("Farm Faction", FarmFaction)]
    route = _route_by_key(selected_key)
    if route is not None and route.key in ("kurzick", "luxon"):
        steps.append(("Donate Faction", DonateFaction))
    return steps
# ---------------------------------------------------------------------------
# Tree creation + UI
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Tree creation + UI
# ---------------------------------------------------------------------------
def _configure_upkeep(tree: BottingTree) -> None:
    tree.Config.ConfigureUpkeep(
        looting_enabled=True,
        resurrection_scroll=True,
        auto_inventory_handler_enabled=True,
        consumable_upkeeps=_enabled_consumable_upkeeps(),
        # The stock recovery only ReturnToOutports the leader, which strands the
        # alt accounts after a wipe. The custom MultiboxPartyWipeRecovery service
        # (registered in ensure_botting_tree) owns wipe recovery with a full team
        # resign, so the stock leader-only service must stay off.
        enable_party_wipe_recovery=False,
    )


def ensure_botting_tree() -> BottingTree:
    global botting_tree
    if botting_tree is None:
        # Native listener: get the leader home quickly on a defeat. On its own it
        # is NOT enough - it only returns the local client, so the custom
        # MultiboxPartyWipeRecovery service below is what resigns the whole team.
        Listeners.AutoReturnOnDefeat.Enable()

        botting_tree = BottingTree.Create(
            MODULE_NAME,
            main_routine=get_execution_steps(),
            routine_name=ROUTINE_NAME,
            repeat=True,
            multi_account=_multi_account,
            auto_loot=True,
            configure_fn=_configure_upkeep,
        )
        # Own party-wipe recovery: on a defeat this resigns EVERY shared-memory
        # account back to the outpost (a solo leader ReturnToOutport cannot
        # reassemble a wiped multibox team) and only then restarts the planner
        # step. Shrine-recoverable wipes restart in place without resigning.
        botting_tree.AddServiceTree("MultiboxPartyWipeRecovery", _multibox_party_wipe_recovery)
        botting_tree.UI.override_draw_config(draw_settings_tab)
        botting_tree.UI.override_draw_help(_draw_help_tab)
        botting_tree.UI._draw_main_child = types.MethodType(_draw_main_child_custom, botting_tree.UI)
    return botting_tree


def draw_settings_tab() -> None:
    """Settings window with the Faction configuration."""
    global selected_key, botting_tree, _multi_account

    if PyImGui.begin_tab_bar("SettingsTabs"):
        if PyImGui.begin_tab_item("Faction"):
            _draw_faction_settings_tab()
            PyImGui.end_tab_item()
        if PyImGui.begin_tab_item("Consumables"):
            _draw_consumables_settings()
            PyImGui.end_tab_item()
        PyImGui.end_tab_bar()


def _draw_consumables_settings() -> None:
    """Cons / Pcons usage toggles; applied live to the running tree."""
    global _activate_conset, _activate_pcons

    PyImGui.text("Consumable Upkeep")
    PyImGui.separator()
    PyImGui.text("Only affects items already in inventory or storage-restocked elsewhere.")

    new_conset = PyImGui.checkbox("Use Consets (Essence / Grail / Armor)", _activate_conset)
    if new_conset != _activate_conset:
        _activate_conset = new_conset
        _apply_consumable_upkeep_change()

    new_pcons = PyImGui.checkbox("Use Pcons (Cupcake, Kabob, Pie, ...)", _activate_pcons)
    if new_pcons != _activate_pcons:
        _activate_pcons = new_pcons
        _apply_consumable_upkeep_change()


def _apply_consumable_upkeep_change() -> None:
    """Re-push the upkeep service so toggles apply without a tree rebuild.

    ConfigureUpkeep only replaces the upkeep trees; it does not reset the
    running planner (same runtime-reconfigure pattern as Shards Of Orr BT).
    """
    if botting_tree is None:
        return
    _configure_upkeep(botting_tree)

# ---------------------------------------------------------------------------
# Statistics tab (per-account title progress; modeled on Sunspear title farm)
# ---------------------------------------------------------------------------
_session_baselines: Dict[str, int] = {}
_session_start_times: Dict[str, float] = {}


def _stats_accounts() -> List[Any]:
    """Accounts shown on the Statistics tab.

    Multibox mode shows every shared-memory account; single-account mode shows
    only the local account (heroes do not carry reputation titles).
    """
    accounts = list(GLOBAL_CACHE.ShMem.GetAllAccountData())
    if _multi_account:
        return accounts
    own_email = str(Player.GetAccountEmail() or "").strip()
    filtered = [
        account for account in accounts
        if str(getattr(account, "AccountEmail", "") or "").strip() == own_email
    ]
    if filtered:
        return filtered
    own_name = str(Player.GetName() or "")
    return [
        account for account in accounts
        if str(getattr(account.AgentData, "CharacterName", "") or "") == own_name
    ]


def _draw_title_track(route: Route) -> None:
    """Per-account title/tier/points-to-next readout for the selected route."""
    global _session_baselines, _session_start_times

    title_idx = int(route.title_id)
    tiers = TITLE_TIERS.get(route.title_id, [])
    now = time.time()
    accounts = _stats_accounts()
    if not accounts:
        PyImGui.text("No account statistics available yet.")
        return

    for account in accounts:
        try:
            name = str(account.AgentData.CharacterName or "Unknown")
            pts = int(account.TitlesData.Titles[title_idx].CurrentPoints)
        except Exception:
            continue

        if name not in _session_baselines:
            _session_baselines[name] = pts
            _session_start_times[name] = now

        tier_name = "Unranked"
        tier_rank = 0
        tier_max_rank = len(tiers)
        next_required = tiers[0].required if tiers else 0
        for index, tier in enumerate(tiers):
            if pts >= tier.required:
                tier_rank = index + 1
                tier_name = tier.name
                next_required = tiers[index + 1].required if index + 1 < len(tiers) else tier.required
            else:
                next_required = tier.required
                break

        gained = pts - _session_baselines[name]
        elapsed = now - _session_start_times[name]
        formatted_time = time.strftime("%H:%M:%S", time.gmtime(elapsed))
        pts_per_hour = int(gained / elapsed * 3600) if elapsed > 0 else 0
        is_maxed = bool(tiers) and pts >= tiers[-1].required
        points_to_next = max(next_required - pts, 0)

        PyImGui.separator()
        PyImGui.text(f"{name} - {tier_name} [{tier_rank}/{tier_max_rank}]")
        PyImGui.text(f"Total Points: {pts:,}")
        if is_maxed:
            PyImGui.text("Next Rank: Maxed")
            PyImGui.text("Points To Go: 0")
            PyImGui.progress_bar(1.0, -1, 0, "Complete")
            PyImGui.text_colored("Maximum rank achieved. Title complete.", (0.4, 1.0, 0.4, 1.0))
        else:
            PyImGui.text(f"Next Rank: {next_required:,}")
            PyImGui.text(f"Points To Go: {points_to_next:,}")
            total = max(next_required, 1)
            PyImGui.progress_bar(min(max(pts, 0) / total, 1.0), -1, 0, f"{max(pts, 0):,} / {total:,}")
        PyImGui.text(f"+{gained:,} points ({pts_per_hour:,}/hr) - Running for: {formatted_time}")


def _active_route() -> Optional[Route]:
    """The route the UI should track.

    In auto-rotate mode this is the route currently being farmed (`_active_farm_key`,
    set live by the goal check). Before the planner has ticked (or if it is not
    currently tracking yet), fall back to the first reputation route that still
    needs work — so the Stats tab shows the real target (e.g. Lightbringer) on a
    character where the other five are already done, instead of the preselected
    Vanguard. In single mode it is simply the selected route.
    """
    if _farm_all:
        if _active_farm_key:
            route = _route_by_key(_active_farm_key)
            if route is not None:
                return route
        for route in _reputation_routes():
            threshold = _goal_threshold(route)
            if threshold is None:
                continue
            if _faction_points(route) < threshold:
                return route
        routes = _reputation_routes()
        return routes[0] if routes else None
    return _route_by_key(selected_key)


def _draw_statistics_tab() -> None:
    """Top-level Statistics tab showing the active route's title progress.

    In auto-rotate mode this follows the route currently being farmed (falling
    back to the next route that still needs work before the planner has ticked);
    in single mode it is the selected route.
    """
    active = _active_route()
    if active is None:
        PyImGui.text("No route selected.")
        return
    if PyImGui.begin_child("ReputationFarmerStatisticsChild", (500, 620), False):
        _draw_title_track(active)
    PyImGui.end_child()




def _draw_help_tab() -> None:
    """Help tab: practical notes for running the Reputation Farmer effectively."""

    def _section(title: str) -> None:
        PyImGui.separator()
        PyImGui.text(title)
        PyImGui.separator()

    _section("What this farm does")
    PyImGui.text_wrapped(
        "Farms faction reputation (title) points for one selected route or for every "
        "rank-tracked route in rotation. Six routes are EotN / Nightfall titles with a "
        "rank ladder (Vanguard, Asuran, Norn, Deldrimor, Sunspear, Lightbringer). "
        "Luxon and Kurzick are the Canthan faction loop: they farm on-hand faction "
        "points and donate them to the guild hall instead of tracking a title rank."
    )

    _section("Two farm modes")
    PyImGui.text_wrapped(
        "Single mode (Farm Faction) runs only the route selected in the picker. "
        "Auto-rotate (Farm All Reputation) runs every rank-tracked route in turn and "
        "skips any route that already reached the target. Toggling the mode rebuilds "
        "the planner and resets the routine to its first step, so pick your mode before "
        "starting a long session."
    )

    _section("Target rank")
    PyImGui.text_wrapped(
        "Rank 5 is where faction skills and most unlocks cap for the EotN / Nightfall "
        "titles, so it is the default stopping point. Max rank pushes a route to the "
        "top of its title tier. The goal is checked live from your title points, so "
        "changing the target mid-run needs no rebuild: routes that already qualify skip "
        "and unfinished ones continue from where they left off."
    )
    PyImGui.text_wrapped(
        "Once every route in the active mode has already reached Rank 5, the Rank 5 "
        "option disappears from the Farm to dropdown and only Max rank remains. Since "
        "all titles are already at the lower target, the bot automatically pushes on "
        "to the top tier. In auto-rotate this applies when all six reputation routes "
        "are at rank 5 or higher; in single-faction mode it applies to the selected "
        "route alone."
    )

    _section("Vanquish vs bounty routes")
    PyImGui.text_wrapped(
        "Vanquish routes enter the explorable map, take the route's shrine blessings, "
        "and clear their kill path. Sunspear and Lightbringer are bounty loops: each "
        "run walks to the bounty priest, accepts the bounty through the dialog, then "
        "clears the kill path. Both loop types travel to the route's outpost on hard "
        "mode once, when their farm loop starts, and resign back to it at the end "
        "of every run."
    )

    _section("Party setup")
    PyImGui.text_wrapped(
        "- Single account: the hero team is sized to the outpost's max party size "
        "(4/6/8 presets). Edit the hero slots and skillbar templates on the Party tab "
        "and Save. The team is re-formed at the start of a run only when a hero is "
        "missing; an intact team is left alone."
    )
    PyImGui.text_wrapped(
        "- This is the multibox build: the bot summons and invites every account "
        "from shared memory, and only re-forms the party when an account is missing "
        "or on a different map. Make sure every account is loaded before you start, "
        "and that the Messaging widget is enabled on every account so the shared "
        "dialog fan-outs are processed."
    )

    _section("Practical tips")
    PyImGui.text_wrapped(
        "- A route's outpost must be unlocked: the bot travels to it every run and "
        "enters the explorable on hard mode."
    )
    PyImGui.text_wrapped(
        "- Luxon / Kurzick blessings require bribing the faction priest. The bot tops "
        "gold up to 500g while in the outpost before each run, so keep at least that "
        "much reachable from storage."
    )
    PyImGui.text_wrapped(
        "- Consumable upkeep: toggle consets and pcons in Settings - Consumables. "
        "Keep them in inventory or storage so the upkeep service can restock."
    )
    PyImGui.text_wrapped(
        "- Runs retry in place (up to 30 minutes) before the planner restarts the "
        "step from the outpost, so a single autopath miss usually does not waste the "
        "run or the party."
    )
    PyImGui.text_wrapped(
        "- Progress is measured by title points (or on-hand faction for Luxon / "
        "Kurzick), not by the number of runs. The farm runs 6 runs per planner pass "
        "and the planner repeats, so leave it alone until the readout shows the "
        "target rank or donate cap is reached."
    )


def _rebuild_main_routine() -> None:
    """Rebuild the planner tree from the current step list (mode / route change).

    The plan step list changes between `Farm Faction` and `Farm All
    Reputation`, and the Donate step only exists for Luxon / Kurzick routes,
    so a rebuild is required whenever those change. Target-rank changes are
    read live and do not need one.
    """
    ensure_botting_tree().SetMainRoutine(
        get_execution_steps(),
        name=ROUTINE_NAME,
        repeat=True,
    )


def _apply_route_selection(new_key: str) -> None:
    """Switch the active faction route and rebuild the planner tree.

    Resets the routine to its first step, which is intended on a faction
    switch.
    """
    global selected_key
    if new_key == selected_key:
        return
    selected_key = new_key
    _rebuild_main_routine()


def _apply_rotation_mode(enable_all: bool) -> None:
    """Turn auto-rotate on/off and rebuild the planner for the new step list.

    The mode changes the plan from `Farm Faction` to `Farm All Reputation`, so
    the tree must be rebuilt; the rank selector inside each mode is read live.
    """
    global _farm_all
    if enable_all == _farm_all:
        return
    _farm_all = enable_all
    _rebuild_main_routine()


def _all_farmed_routes_at_target() -> bool:
    """True when every account on every farmed route is already at the target rank.

    Auto-rotate checks all reputation routes; single mode checks just the
    selected route. When true, the "Rank {_target_rank}" selector option is
    hidden so the only forward choice is Max rank.

    Account-aware: a maxed leader must not hide the rank option while the rest
    of the team is still below the target, matching the account-aware goal
    check in _build_farm_sequence.
    """
    routes = _reputation_routes() if _farm_all else [r for r in [_route_by_key(selected_key)] if r]
    accounts = _all_active_accounts()
    if not routes or not accounts:
        return False
    return all(_account_rank(account, route) >= _target_rank for route in routes for account in accounts)


def _draw_farm_controls(id_suffix: str = "") -> None:
    """Rotation-mode + target-rank controls, shared by the Settings and Main tabs.

    Auto-rotate farms every reputation route (the six EotN / Nightfall titles)
    to the target; single mode farms only `selected_key`. The rank selector
    picks the stopping point: Rank 5 (where faction skills / most unlocks cap)
    or Max rank. Toggling the mode rebuilds the planner (the step list changes
    between `Farm Faction` and `Farm All Reputation`); the rank selector is
    read live by the goal check, so it needs no rebuild.

    `id_suffix` keeps ImGui widget IDs unique when this is drawn in more than
    one place at once (Main body + Settings tab), or ImGui raises conflicting
    ID errors.
    """
    global _farm_all, _farm_to_max
    new_all = PyImGui.checkbox(f"Auto-rotate all reputation factions##{id_suffix}", _farm_all)
    if new_all != _farm_all:
        _apply_rotation_mode(new_all)
    only_max = _all_farmed_routes_at_target()
    if only_max and not _farm_to_max:
        _farm_to_max = True
    target_labels = ["Max rank"] if only_max else [f"Rank {_target_rank}", "Max rank"]
    target_index = 0 if only_max else (1 if _farm_to_max else 0)
    new_index = PyImGui.combo(f"Farm to##{id_suffix}", target_index, target_labels)
    if new_index != target_index:
        _farm_to_max = bool(new_index)


def _draw_route_selector() -> None:
    """Radio-list farm picker shared by the Settings and Main tabs."""
    route_index_by_key = {route.key: index for index, route in enumerate(ALL_ROUTES)}
    selected_index = route_index_by_key.get(selected_key, 0)

    for index, route in enumerate(ALL_ROUTES):
        label = route.name + ("  (bounty loop)" if route.bounty else "")
        selected_index = PyImGui.radio_button(label, selected_index, index)

    _apply_route_selection(ALL_ROUTES[selected_index].key)


def _draw_route_readout(route: Route) -> None:
    points = _faction_points(route)
    tiers = TITLE_TIERS.get(int(route.title_id), [])
    tier_name = "Unranked"
    for tier in tiers:
        if points >= tier.required:
            tier_name = tier.name
    PyImGui.text(f"{route.name} points: {points:,}")
    PyImGui.text(f"Current tier: {tier_name}")
    threshold = _goal_threshold(route)
    if route.key in ("kurzick", "luxon"):
        goal = f"donate cap ({threshold:,} on-hand)" if threshold is not None else "donate cap"
    else:
        goal_rank = _route_goal_rank(route)
        target = "max rank" if goal_rank is None else f"rank {goal_rank}"
        goal = f"{target} ({threshold:,} points)" if threshold is not None else target
    PyImGui.text(f"Session goal: {goal}")


def _draw_faction_settings_tab() -> None:
    """Faction selector + rotation / target + multibox toggle + live readout."""
    PyImGui.text("Faction")
    PyImGui.separator()

    _draw_farm_controls("Settings")

    PyImGui.separator()

    if _farm_all:
        PyImGui.text("Auto-rotate: every reputation faction is farmed to the target rank.")
        PyImGui.text("The picker below only applies in single-faction mode.")
        PyImGui.separator()

    _draw_route_selector()

    PyImGui.separator()
    display = _active_route()
    if display is not None:
        _draw_route_readout(display)


def _draw_main_child_custom(
    self,
    main_child_dimensions=(350, 300),
    icon_path="",
    iconwidth=96,
) -> None:
    """Custom Main-tab body for the Reputation Farmer.

    Bound onto botting_tree.UI in ensure_botting_tree() so the selected farm
    shows as a dropdown where the framework normally draws the planner "Start
    At" list, with the Start control directly below it.
    """
    status = self._main_status_snapshot()
    if PyImGui.begin_table(
        "botting_tree_header_table",
        2,
        PyImGui.TableFlags.RowBg | PyImGui.TableFlags.BordersOuterH,
    ):
        PyImGui.table_setup_column("Icon", PyImGui.TableColumnFlags.WidthFixed, iconwidth)
        PyImGui.table_setup_column("Status", PyImGui.TableColumnFlags.WidthFixed, main_child_dimensions[0] - iconwidth)
        PyImGui.table_next_row()
        PyImGui.table_set_column_index(0)
        self._draw_texture(icon_path, (float(iconwidth), float(iconwidth)))
        PyImGui.table_set_column_index(1)
        PyImGui.text(self.parent.bot_name)
        if _farm_all:
            active = _active_route()
            active_name = active.name if active else "All reputation factions"
            PyImGui.text(f"Current farm: Auto-rotate -> {active_name}")
        else:
            selected = _route_by_key(selected_key)
            current_farm = selected.name if selected else selected_key
            PyImGui.text(f"Current farm: {current_farm}")
        PyImGui.text(f"HeroAI: {self.parent.GetBlackboardValue('HEROAI_STATUS', 'Idle')}")
        PyImGui.text(f"Planner: {self.parent.GetBlackboardValue('PLANNER_STATUS', 'Idle')}")
        PyImGui.end_table()

    _draw_farm_controls("Main")
    if _farm_all:
        PyImGui.text("Auto-rotate: every reputation faction farmed to the target rank.")
    else:
        route_index_by_key = {route.key: index for index, route in enumerate(ALL_ROUTES)}
        current_index = route_index_by_key.get(selected_key, 0)
        route_labels = [route.name for route in ALL_ROUTES]
        selected_index = PyImGui.combo("Selected Farm", current_index, route_labels)
        if selected_index != current_index:
            _apply_route_selection(ALL_ROUTES[selected_index].key)

    if self.parent.IsStarted():
        if PyImGui.button("Stop##BottingTreeStop"):
            self.parent.Stop()
        PyImGui.same_line(0, -1)
        if self.parent.IsPaused():
            if PyImGui.button("Resume##BottingTreePause"):
                self.parent.Pause(False)
        else:
            if PyImGui.button("Pause##BottingTreePause"):
                self.parent.Pause(True)
    else:
        if PyImGui.button("Start##BottingTreeStart"):
            self.parent.Start()

    PyImGui.separator()
    self._colored_bool("Started", status["started"])
    self._colored_bool("Paused", status["paused"])
    self._colored_bool("Headless HeroAI Enabled", status["headless_heroai_enabled"])
    self._colored_bool("Looting Enabled", status["looting_enabled"])
    self._colored_bool("Resurrection Scroll Enabled", status["resurrection_scroll_enabled"])
    self._colored_bool("Account Isolation Enabled", status["account_isolation_enabled"])
    self._colored_bool("Pause On Combat Enabled", status["pause_on_combat_enabled"])
    self._colored_bool("Combat Routine Active", status["combat_active"])
    self._colored_bool("Loot Routine Active", status["looting_active"])


def main() -> None:
    global initialized
    if not initialized:
        ensure_botting_tree()
        initialized = True
    tree = ensure_botting_tree()
    tree.tick()
    extra_tabs: List[Tuple[str, Callable[[], None]]] = [("Statistics", _draw_statistics_tab)]
    tree.UI.draw_window(
        icon_path=os.path.join(PySystem.Console.get_projects_path(), MODULE_ICON),
        main_child_dimensions=(520, 420),
        extra_tabs=extra_tabs,
    )


def tooltip() -> str:
    return MODULE_NAME + " - selectable all-in-one faction/title farmer (BT)."
