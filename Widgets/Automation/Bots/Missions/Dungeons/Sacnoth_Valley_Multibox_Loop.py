from __future__ import annotations

from collections.abc import Callable

from Py4GWCoreLib.BottingTree import BottingTree
from Py4GWCoreLib.Listeners import Listeners
from Py4GWCoreLib.enums_src.GameData_enums import Range
from Py4GWCoreLib.enums_src.Player_enums import PlayerStatus
from Py4GWCoreLib.native_src.internals.types import Vec2f
from Py4GWCoreLib.py4gwcorelib_src.BehaviorTree import BehaviorTree
from Sources.ApoSource.ApoBottingLib import wrappers as BT


MODULE_NAME = "Sacnoth Valley Multibox Loop"
MODULE_CATEGORY = "Automation"
MODULE_TAGS = ["Sacnoth Valley", "Grothmar Wardowns", "Multibox", "Farm"]
MODULE_DESCRIPTION = "Repeatable multibox route: Grothmar Wardowns -> Sacnoth Valley -> Grothmar Wardowns."

GROTHMAR_WARDOWNS = 649
SACNOTH_VALLEY = 651

# Coordinates supplied in the requested order. Do not invert the portals.
GROTHMAR_TO_SACNOTH = Vec2f(22890, -13364)
SACNOTH_TO_GROTHMAR = Vec2f(-19710,16253)

SACNOTH_ROUTE = [
    (-16339, 16056),
    (-14421, 14735),
    (-14075, 13099),
    (-11810, 11684),
    (-10308, 12171),
    (-9360, 11872),
    (-11431, 11676),
    (-14410, 13274),
]

initialized = False
botting_tree: BottingTree | None = None


def _configure_botting_tree(tree: BottingTree) -> None:
    # Same multibox/recovery configuration principle as Shards of Orr BT.
    tree.Config.ConfigureUpkeep(
        looting_enabled=True,
        resurrection_scroll=True,
        auto_inventory_handler_enabled=True,
        enable_party_wipe_recovery=True,
        enable_nearest_shrine_recovery=True,
        heroai_state_logging=False,
    )


def ensure_botting_tree() -> BottingTree:
    global botting_tree

    if botting_tree is None:
        Listeners.AutoReturnOnDefeat.Enable()
        botting_tree = BottingTree.Create(
            MODULE_NAME,
            main_routine=get_execution_steps(),
            routine_name="MultiAccountSequence",
            repeat=True,
            multi_account=True,
            isolation_enabled=False,
            configure_fn=_configure_botting_tree,
        )

    return botting_tree


def InitializeBot() -> BehaviorTree:
    bot = ensure_botting_tree()
    return BT.Sequence(
        name="Initialize Sacnoth Multibox",
        children=[
            bot.Config.Aggressive(
                multi_account=True,
                auto_loot=True,
                resurrection_scroll=True,
                account_isolation=False,
            ),
            BT.SetPlayerStatus(PlayerStatus.Offline, log=True),
            BT.LogMessage(message="Sacnoth multibox loop initialized.", module_name=MODULE_NAME),
        ],
    )


def EnterSacnoth() -> BehaviorTree:
    # Re-launching the bot while already in Sacnoth must not send it back to 649.
    already_inside = BT.Sequence(
        name="Already In Sacnoth Valley",
        children=[
            BT.IsCurrentMap(map_id=SACNOTH_VALLEY, log=False),
            BT.Succeeder("SacnothAlreadyEntered"),
        ],
    )

    enter_from_grothmar = BT.Sequence(
        name="Grothmar -> Sacnoth",
        children=[
            BT.IsCurrentMap(map_id=GROTHMAR_WARDOWNS, log=True),
            BT.MoveAndExitMap(GROTHMAR_TO_SACNOTH, target_map_id=SACNOTH_VALLEY, log=True),
            BT.WaitForMapLoad(map_id=SACNOTH_VALLEY, timeout_ms=30_000),
            BT.WaitUntilOnExplorable(timeout_ms=30_000),
            BT.Wait(1_000),
        ],
    )

    return BT.Selector(
        name="Enter Sacnoth Valley",
        children=[already_inside, enter_from_grothmar],
    )


def _vanquish_point_steps() -> list[tuple[str, Callable[[], BehaviorTree]]]:
    # One waypoint = one Planner step, as in Shards of Orr.
    steps: list[tuple[str, Callable[[], BehaviorTree]]] = []

    for index, point in enumerate(SACNOTH_ROUTE, start=1):
        name = f"Sacnoth - Vanquish {index:02d}"
        steps.append((
            name,
            lambda point=point, name=name: BT.Sequence(
                name=name,
                children=[
                    BT.IsCurrentMap(map_id=SACNOTH_VALLEY, log=False),
                    BT.VanquishNode(
                        steps=[point],
                        name=name,
                        clear_area_radius=Range.Spirit.value,
                        move_tolerance=500.0,
                        flag_heroes_to_waypoint=False,
                        log=False,
                    ),
                ],
            ),
        ))

    return steps


def ReturnToGrothmar() -> BehaviorTree:
    return BT.Sequence(
        name="Sacnoth -> Grothmar",
        children=[
            BT.IsCurrentMap(map_id=SACNOTH_VALLEY, log=True),
            BT.MoveAndExitMap(SACNOTH_TO_GROTHMAR, target_map_id=GROTHMAR_WARDOWNS, log=True),
            BT.WaitForMapLoad(map_id=GROTHMAR_WARDOWNS, timeout_ms=30_000),
            BT.WaitUntilOnExplorable(timeout_ms=30_000),
            BT.Wait(1_000),
        ],
    )


def get_execution_steps() -> list[tuple[str, Callable[[], BehaviorTree]]]:
    return [
        ("Initialize Bot", InitializeBot),
        ("Grothmar -> Sacnoth (651)", EnterSacnoth),
        *_vanquish_point_steps(),
        ("Sacnoth -> Grothmar (649)", ReturnToGrothmar),
    ]


def main() -> None:
    global initialized

    if not initialized:
        ensure_botting_tree()
        initialized = True

    tree = ensure_botting_tree()
    tree.tick()
    tree.UI.draw_window()


if __name__ == "__main__":
    main()
