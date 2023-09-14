from typing import *
from .fetch import parse_game_link
from .injustices import evaluate_game

# This file is the entry point for InjusticeJudge.
# Essentially calls `parse_game_link` from `fetch.py`
# and gives the result to `evaluate_injustices` from `injustices.py`.

async def analyze_game(link: str, specified_player = None, look_for: Set[str] = {"injustice"}) -> List[str]:
    """Given a game link, fetch and parse the game into kyokus, then evaluate each kyoku"""
    # print(f"Analyzing game {link}:")
    kyokus, game_metadata, player = await parse_game_link(link, specified_player)

    # # debug hand printing, by printing all final hands assuming they are tenpai
    # from .utils import sorted_hand, try_remove_all_tiles, print_full_hand, round_name
    # for kyoku in kyokus:
    #     for winner in range(4):
    #         final_closed_hand = sorted_hand(try_remove_all_tiles(tuple(kyoku.hands[winner]), tuple(kyoku.calls[winner])))
    #         final_waits = kyoku.hands[winner].shanten[1]
    #         final_ukeire = kyoku.final_ukeire[winner]
    #         final_call_info = kyoku.call_info[winner]
    #         final_tile = kyoku.final_discard if kyoku.result[0] == "ron" else kyoku.final_draw
    #         furiten = kyoku.furiten[winner]
    #         print(round_name(kyoku.round, kyoku.honba), print_full_hand(final_closed_hand, final_call_info, (0, final_waits), final_ukeire, final_tile, furiten), final_tile, furiten)
    return [result for kyoku in kyokus for result in evaluate_game(kyoku, player, look_for)]
