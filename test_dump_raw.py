import asyncio
import json
from data.onexbet import scrape_onexbet

async def test():
    # To dump the raw data, let's just patch filter_event_list_matches temporarily in our process memory
    import data.onexbet as mx
    old_filter = mx.filter_event_list_matches
    def my_filter(all_items, comp_map, league_map, odds_map):
        with open("c:\\quant_bet_alpha\\raw_items.json", "w") as f:
            json.dump(all_items, f)
        with open("c:\\quant_bet_alpha\\raw_comp.json", "w") as f:
            json.dump(comp_map, f)
        return old_filter(all_items, comp_map, league_map, odds_map)
    mx.filter_event_list_matches = my_filter
    
    old_filter_lf = mx.filter_linefeed_matches
    def my_filter_lf(raw_events):
        with open("c:\\quant_bet_alpha\\raw_events_lf.json", "w") as f:
            json.dump(raw_events, f)
        return old_filter_lf(raw_events)
    mx.filter_linefeed_matches = my_filter_lf

    await mx.scrape_onexbet()

if __name__ == "__main__":
    asyncio.run(test())
