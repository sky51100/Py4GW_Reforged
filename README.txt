Fix: Summoning Stone detection in Py4GW_Reforged

Archive contents:
  Py4GWCoreLib/Item.py (complete updated source file)

Installation:
1. Close every Py4GW client.
2. Back up your current Py4GWCoreLib/Item.py.
3. Extract the ZIP into the Py4GW_Reforged root folder and confirm overwrite of Py4GWCoreLib/Item.py.
4. Restart all Py4GW clients.

Change:
 has_active_party_summon checks Party.GetOthers() AND AgentArray.GetAllyArray(),
 excludes pets and supports struct entries. This helps detect allied Angchu
 summoned creatures even when they are missing from Party.GetOthers().

Base source: Item.py from the user-provided Work-Branch-merchant-rules-updated ZIP.
Only this module is included. If your local Item.py has newer edits than
that archive, compare before overwriting.
