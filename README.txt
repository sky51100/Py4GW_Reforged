Summoning Stone Party Service - Core + Shards of Orr

Files included:
- Py4GWCoreLib/botting_tree_src/services.py
- Py4GWCoreLib/botting_tree_src/upkeep.py
- Py4GWCoreLib/Item.py
- Widgets/Automation/Bots/Missions/Dungeons/Shards of Orr BT.py

What changes:
1. Adds reusable BottingTree SummoningStonePartyServiceTree.
2. Adds Add/EnsureSummoningStonePartyService helpers.
3. Service coordinates active multibox accounts one-by-one using the existing
   SharedCommandType.UseSummoningStone / Messaging.UseSummoningStone handler.
4. Shards no longer contains its own summoning service or Level1/2/3 summon calls.
5. Shards keeps its Use summoning stones toggle; the Core service reads it live.
6. Active summon detection scans Party.GetOthers() broadly, but scans the full
   AllyArray strictly by known summon ModelID/encoded name to avoid false positives
   from ordinary allied NPCs while still detecting Angchu/Tengu summons.

Install:
Extract this ZIP at the Py4GW_Reforged repository root and replace the four files.
Restart every Py4GW client afterwards.
