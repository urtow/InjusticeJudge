import functools
from .constants import TOGGLE_RED_FIVE
from typing import *

###
### utility functions
###

def pt(tile: int) -> str:
    """print tile (2-char representation)"""
    TILE_REPRS = "🀇🀈🀉🀊🀋🀌🀍🀎🀏🀙🀚🀛🀜🀝🀞🀟🀠🀡🀐🀑🀒🀓🀔🀕🀖🀗🀘🀀🀁🀂🀃🀆🀅🀄︎"
    if tile < 20:
        return TILE_REPRS[tile - 11] + " "
    elif tile < 30:
        return TILE_REPRS[tile - 21 + 9] + " "
    elif tile < 40:
        return TILE_REPRS[tile - 31 + 18] + " "
    elif tile < 47:
        return TILE_REPRS[tile - 41 + 27] + " "
    elif tile == 47:
        # need to specially output 🀄︎ so it's not an emoji
        return TILE_REPRS[-2:]
    elif tile == 51:
        return "🀋·"
    elif tile == 52:
        return "🀝·"
    elif tile == 53:
        return "🀔·"
    else:
        return "??"
ph = lambda hand: "".join(map(pt, hand)) # print hand
remove_red_five = lambda tile: TOGGLE_RED_FIVE[tile] if tile in {51,52,53} else tile
remove_red_fives = lambda hand: map(remove_red_five, hand)
sorted_hand = lambda hand: tuple(sorted(hand, key=remove_red_five))
round_name = lambda rnd, honba: (f"East {rnd+1}" if rnd <= 3 else f"South {rnd-3}") + ("" if honba == 0 else f"-{honba}")
relative_seat_name = lambda you, other: {0: "self", 1: "shimocha", 2: "toimen", 3: "kamicha"}[(other-you)%4]

@functools.cache
def try_remove_all_tiles(hand: Tuple[int], tiles: Tuple[int]) -> Tuple[int, ...]:
    """
    Tries to remove all of `tiles` from `hand`. If it can't, returns `hand` unchanged
    """
    hand_copy = list(hand)
    for tile in tiles:
        if tile in hand_copy or tile in TOGGLE_RED_FIVE and (tile := TOGGLE_RED_FIVE[tile]) in hand_copy:
            hand_copy.remove(tile)
        else:
            return tuple(hand)
    return tuple(hand_copy)