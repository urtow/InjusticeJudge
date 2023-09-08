from dataclasses import dataclass, field
from enum import IntEnum
import functools
from typing import *

from .constants import TOGGLE_RED_FIVE, OYA_RON_SCORE, KO_RON_SCORE, OYA_TSUMO_SCORE, KO_TSUMO_SCORE, TRANSLATE
from .utils import ph, pt, pt_sideways, normalize_red_five, normalize_red_fives, shanten_name, sorted_hand, try_remove_all_tiles
from .shanten import calculate_shanten

# This file contains most of the classes used in InjusticeJudge.
# It also contains some printing logic in the form of __str__ overloads.

class Dir(IntEnum):
    """Enum representing a direction, add to a seat mod 4 to get the indicated seat"""
    SELF = 0
    SHIMOCHA = 1
    TOIMEN = 2
    KAMICHA = 3

@dataclass(frozen=True)
class CallInfo:
    """Immutable object describing a single call (chii, pon, daiminkan, ankan, kakan)"""
    type: str        # one of "chii", "pon", "minkan", "ankan", "kakan"
    tile: int        # the called tile
    dir: Dir         # where the tile was called from (indicates where to point the called tile)
    tiles: List[int] # the 3 or 4 tiles set aside after calling
    def __post_init__(self):
        super().__setattr__("tiles", sorted_hand(self.tiles))
    def to_str(self, doras=[], uras=[]):
        as_dora = lambda tile: tile + (100 if tile in doras or tile in uras else 0)
        tiles = tuple(map(as_dora, self.tiles))
        tile = as_dora(self.tile)
        # other_tiles is all the non-called tiles in the call
        other_tiles = try_remove_all_tiles(tiles, (tile,))
        assert tile is not None, "CallInfo has a `None` tile??"
        sideways = pt_sideways(tile)
        if self.type == "ankan":
            if any(tile in {51,52,53} for tile in tiles):
                return ph((50, TOGGLE_RED_FIVE[tile], tile, 50))
            else:
                return ph((50, tile, tile, 50))
        elif self.type == "kakan": # print two consecutive sideways tiles
            sideways = pt_sideways(other_tiles[0]) + sideways
            other_tiles = other_tiles[1:]
        if self.dir == Dir.SHIMOCHA:
            return ph(other_tiles) + sideways
        elif self.dir == Dir.TOIMEN:
            return pt(other_tiles[0]) + sideways + ph(other_tiles[1:])
        elif self.dir == Dir.KAMICHA:
            return sideways + ph(other_tiles)
        # dir == Dir.SELF is only for ankan and is handled above
    def __str__(self):
        return self.to_str()
    

@functools.cache
def _hidden_part(hand: Tuple[int], calls: Tuple[int]) -> Tuple[int, ...]:
    """Cached helper for getting the hidden part of a hand, used below in __post_init__"""
    ret = try_remove_all_tiles(hand, calls)
    assert len(ret) + len(calls) == len(hand), f"with hand = {ph(hand)} and calls = {ph(calls)}, somehow hidden part is {ph(ret)}"
    return ret

# main hand class
@dataclass(frozen=True)
class Hand:
    """Immutable object describing the state of a single hand"""
    tiles: Tuple[int, ...]                              # all tiles in the hand
    calls: List[CallInfo] = field(default_factory=list) # every call the hand has made, in order
    open_part: Tuple[int, ...] = ()                     # all tiles currently shown as a call
    hidden_part: Tuple[int, ...] = ()                   # tiles - open_part
    closed_part: Tuple[int, ...] = ()                   # hidden_part + any ankans
    shanten: Tuple[float, List[int]] = (-1, [])         # shanten for the hand, or -1 if the hand is 14 tiles
                                                        # (like when it's in the middle of a draw or call)
    prev_shanten: Tuple[float, List[int]] = (-1, [])    # shanten for the hand right before said draw or call
    kita_count: int = 0                                 # number of kita calls for this hand
    def __post_init__(self):
        """You only need to provide `tiles` (and `calls`, if any), this calculates the rest"""
        super().__setattr__("tiles", sorted_hand(self.tiles))
        super().__setattr__("open_part", tuple(tile for call in self.calls for tile in call.tiles[:3]))
        super().__setattr__("hidden_part", _hidden_part(self.tiles, self.open_part))
        # for closed part, add any ankan back in as triplets
        closed_part = self.hidden_part
        for call in self.calls:
            if call.type == "ankan":
                closed_part = (*closed_part, call.tile, call.tile, call.tile)
        super().__setattr__("closed_part", closed_part)
        if len(self.tiles) in {1, 4, 7, 10, 13}:
            super().__setattr__("shanten", calculate_shanten(self.hidden_part))
        else:
            super().__setattr__("shanten", self.prev_shanten)
    def to_str(self, doras=[], uras=[]):
        to_str = lambda call: call.to_str(doras, uras)
        call_string = "" if len(self.calls) == 0 else "\u2007" + "\u2007".join(map(to_str, reversed(self.calls)))
        as_dora = lambda tile: tile + (100 if tile in doras or tile in uras else 0)
        hidden_part = tuple(map(as_dora, self.hidden_part))
        return f"{ph(hidden_part)}{call_string}"
    def __str__(self):
        return self.to_str()
    def __hash__(self):
        return hash((self.open_part, self.closed_part))

    def add(self, tile: int) -> "Hand":
        """Immutable update for drawing a tile"""
        return Hand((*self.tiles, tile), [*self.calls], prev_shanten=self.shanten, kita_count=self.kita_count)
    def add_call(self, calls: CallInfo) -> "Hand":
        """Immutable update for calling a tile"""
        return Hand(self.tiles, [*self.calls, calls], prev_shanten=self.shanten, kita_count=self.kita_count)
    def remove(self, tile: int) -> "Hand":
        """Immutable update for discarding a tile"""
        tiles = list(self.tiles)
        tiles.remove(tile)
        return Hand(tuple(tiles), [*self.calls], prev_shanten=self.shanten, kita_count=self.kita_count)
    def kakan(self, called_tile: int):
        """Immutable update for adding a tile to an existing pon call (kakan)"""
        pon_index = next((i for i, calls in enumerate(self.calls) if calls.type == "pon" and normalize_red_five(calls.tile) == normalize_red_five(called_tile)), None)
        assert pon_index is not None, f"unable to find previous pon in calls: {self.calls}"
        orig_direction = self.calls[pon_index].dir
        orig_tiles = [*self.calls[pon_index].tiles, called_tile]
        calls_copy = [*self.calls]
        calls_copy[pon_index] = CallInfo("kakan", called_tile, orig_direction, orig_tiles)
        return Hand(self.tiles, calls_copy, prev_shanten=self.shanten, kita_count=self.kita_count)
    def kita(self):
        """Immutable update for adding kita"""
        return Hand(self.tiles, self.calls, prev_shanten=self.prev_shanten, kita_count=self.kita_count+1)
    def print_hand_details(self,
                           ukeire: int,
                           final_tile: Optional[int] = None,
                           furiten: bool = False,
                           doras: List[int] = [],
                           uras: List[int] = []) -> str:
        """print this hand + calls + optional final tile + furiten state + shanten/waits + number of ukeire"""
        wait_string = ""
        win_string = ""
        as_dora = lambda tile: tile + (100 if tile in doras or tile in uras else 0)
        if self.shanten[0] == 0:
            wait_string = f"{' (furiten) ' if furiten else ' '}waits: {ph(sorted_hand(self.shanten[1]))} ({ukeire} out{'s' if ukeire > 1 else ''})"
            win_string = f"\u2007{pt(as_dora(final_tile))}" if final_tile is not None else ""
        elif self.shanten[0] > 0:
            wait_string = f" ({shanten_name(self.shanten)})"
        return f"{self.to_str(doras, uras)}{win_string}{wait_string}"
    def ukeire(self, visible: Iterable[int]):
        """
        Pass in all the visible tiles on board (not including hand).
        Return the ukeire of the hand, or 0 if the hand is not tenpai.
        """
        shanten, waits = self.shanten
        if shanten > 0:
            return 0
        relevant_tiles = set(normalize_red_fives(waits))
        visible = list(normalize_red_fives(list(self.tiles) + list(visible)))
        return 4 * len(relevant_tiles) - sum(visible.count(wait) for wait in relevant_tiles)

# hand interpretations and yaku
@dataclass
class Interpretation:
    """A single interpretation of a single hand (decomposed into triplets, sequences, and pair)"""
    hand: Tuple[int, ...]                           # The non-decomposed part of the original hand
    ron_fu: int = 0                                 # ron fu using this interpretation of the hand (not rounded)
    tsumo_fu: int = 0                               # tsumo fu using this interpretation of the hand (not rounded)
    sequences: Tuple[Tuple[int, ...], ...] = ()     # Sequences taken from the original hand
    triplets: Tuple[Tuple[int, ...], ...] = ()      # Triplets taken from the original hand
    pair: Optional[Tuple[int, int]] = None          # A pair taken from the original hand
    calls: Tuple[CallInfo, ...] = ()                # A frozen list of calls from the original hand
    def unpack(self):
        return (self.hand, self.ron_fu, self.tsumo_fu, self.sequences, self.triplets, self.pair)
    def __hash__(self):
        return hash(self.unpack())
    def __str__(self):
        full_hand = (*self.sequences, *self.triplets, self.pair, self.hand) if self.pair is not None else (*self.sequences, *self.triplets, self.hand)
        return " ".join(map(ph, full_hand)) + f" ron {self.ron_fu} tsumo {self.tsumo_fu}"

YakuForWait = Dict[int, List[Tuple[str, int]]]
@dataclass
class Score:
    """Generated score for a given hand (does NOT come from parsed game result scores)"""
    yaku: List[Tuple[str, int]] # list of ("yaku name", han value)
    han: int                    # total han for those yaku
    fu: int                     # total fu for some interpretation of the hand
    tsumo: bool
    def __hash__(self):
        return hash((self.fu, tuple(self.yaku)))
    def __lt__(self, other):
        return (self.han, self.fu) < (other.han, other.fu)
    def __str__(self):
        ret = f"{self.han}/{self.fu} {self.yaku}"
        if self.interpretation is not None:
            ret += f" ({self.interpretation!s})"
        return ret
    def add_dora(self, dora_type: str, amount: int):
        # get the current amount
        i = self.get_dora_index(dora_type)
        new_value = self.yaku[i][1] + amount
        # apply the change
        if new_value == 0:
            if new_value != amount: # there was some preexisting dora
                del self.yaku[i]
        else:
            new_dora = (dora_type + (f" {new_value}" if new_value > 1 else ""), new_value)
            if new_value != amount: # there was some preexisting dora
                self.yaku[i] = new_dora
            else:
                self.yaku.append(new_dora)
        self.han += amount
    def get_value(self, num_players: int, is_dealer: bool):
        if self.tsumo:
            oya = OYA_TSUMO_SCORE[self.han][self.fu]  # type: ignore[index]
            ko = oya if is_dealer else KO_TSUMO_SCORE[self.han][self.fu]  # type: ignore[index]
            return oya + (num_players-2)*ko
        else:
            return (OYA_RON_SCORE if is_dealer else KO_RON_SCORE)[self.han][self.fu]  # type: ignore[index]
    def to_score_deltas(self, round: int, honba: int, num_players: int, winners: List[int], payer: int) -> List[int]:
        score_deltas = [0]*num_players
        if self.tsumo:
            assert len(winners) == 1
            for payer in {0,1,2,3} - {winners[0]}:
                oya_payment = (winners[0] == round%4) or (payer == round%4)
                score_deltas[payer] -= (OYA_TSUMO_SCORE if oya_payment else KO_TSUMO_SCORE)[self.han][self.fu]  # type: ignore[index]
                score_deltas[payer] -= 100 * honba
            score_deltas[payer] -= sum(score_deltas)
        else:
            for winner in winners:
                oya_payment = winner == round%4
                score_deltas[winner] += (OYA_RON_SCORE if oya_payment else KO_RON_SCORE)[self.han][self.fu]  # type: ignore[index]
                score_deltas[winner] += 300 * honba
            score_deltas[winner] -= sum(score_deltas)
        return score_deltas
    def has_riichi(self):
        return ("riichi", 1) in self.yaku
    def has_ippatsu(self):
        return ("ippatsu", 1) in self.yaku
    def has_haitei(self):
        return ("haitei", 1) in self.yaku or ("houtei", 1) in self.yaku
    def get_dora_index(self, dora_type) -> Optional[int]:
        for i, (name, value) in enumerate(self.yaku):
            if name.startswith(dora_type):
                return i
        return None
    def count_dora(self):
        dora_index = self.get_dora_index("dora")
        aka_index = self.get_dora_index("aka")
        dora = self.yaku[dora_index][1] if dora_index is not None else 0
        aka = self.yaku[aka_index][1] if aka_index is not None else 0
        return dora + aka
    def count_ura(self):
        ura_index = self.get_dora_index("ura")
        ura = self.yaku[ura_index][1] if ura_index is not None else 0
        return ura
    # these fields are only for debug use
    interpretation: Optional[Interpretation] = None # the interpretation used to calculate yaku and fu
    hand: Optional[Hand] = None                     # the original hand

@dataclass
class ResultYakuList:
    """Parsed version of tenhou's game result yaku list"""
    yaku_strs: List[str]   # the raw list of yaku
    kita: int              # count of kita (sanma)

    # these are generated by post_init based on the above:
    yaku: List[Tuple[str, int]] = field(default_factory=list) # the parsed list of yaku
    def __post_init__(self) -> None:
        # this does a couple things:
        # - convert yaku_strs=["立直(1飜)", "一発(1飜)"] into yaku=[("riichi", 1), ("ippatsu", 1)]
        # - Tenhou sama counts kita as normal dora, we reverse that
        # - check for riichi, ippatsu, and haitei/houtei
        dora_index: Optional[int] = None
        has_yakuman = False
        yaku_dora = 0
        yaku_kita = 0
        if not any("役満" in y for y in self.yaku_strs): # not a yakuman hand
            for i, y in enumerate(self.yaku_strs):
                name_str, value_str = y.split("(")
                name = TRANSLATE[name_str]
                value = int(value_str.split("飜")[0])
                if name in "dora":
                    dora_index = i # keep track of where "dora" is in the list
                    yaku_dora += value
                elif name == "aka":
                    yaku_dora += value
                elif name == "kita":
                    yaku_kita = value
            if self.kita > 0 and yaku_kita == 0:
                assert dora_index is not None, f"somehow we know there's {self.kita} kita, but tenhou didn't count it as dora?"
                # must be a Tenhou sanma game hand with kita because
                # it counts kita as regular dora (not "抜きドラ")
                non_kita_dora_count = yaku_dora - self.kita
                assert non_kita_dora_count >= 0
                if non_kita_dora_count == 0:
                    del self.yaku_strs[dora_index]
                else:
                    self.yaku_strs[dora_index] = f"ドラ({non_kita_dora_count}飜)"
                self.yaku_strs.append(f"抜きドラ({self.kita}飜)")
                self.kita = self.kita
                self.dora = non_kita_dora_count
        for i, y in enumerate(self.yaku_strs):
            name_str, value_str = y.split("(")
            name = TRANSLATE[name_str]
            value = 13 if "役満" in value_str else int(value_str.split("飜")[0])
            if name in {"dora", "aka", "ura", "kita"} and value > 1:
                name = f"{name} {value}"
            self.yaku.append((name, value))
    def to_score(self, fu = 0, is_tsumo: bool = False) -> Score:
        han = sum(value for _, value in self.yaku)
        return Score(self.yaku, han, fu, is_tsumo)


@dataclass(frozen = True)
class Win:
    score_delta: List[int]  # list of score differences for this round
    winner: int             # winner's seat (0-3)
    dama: bool              # whether it was a dama hand or not
    limit_name: str         # e.g. "mangan", or empty string if not a limit hand
    score: Score            # Score object (contains han, fu, score, and yaku)
    yaku: ResultYakuList    # parsed yaku list
@dataclass(frozen = True)
class Ron(Win):
    """Parsed version of a single tenhou ron result"""
    won_from: Optional[int] # loser's seat (0-3)
@dataclass(frozen = True)
class Tsumo(Win):
    """Parsed version of a tenhou tsumo result"""
    pass
@dataclass(frozen = True)
class Draw:
    """Parsed version of a tenhou ryuukyoku or any draw result"""
    score_delta: List[int] # list of score differences for this round
    name: str              # name of the draw, e.g. "ryuukyoku"


Event = Tuple[Any, ...]
@dataclass
class Kyoku:
    """
    Main state object representing a single round
    Generated in `fetch.py` for use in `flags.py`
    The idea is to store enough data to be able to get all the facts about a round
    This gets turned into a list of facts about a round (see flags.py)
    """
    # Some basic facts about this kyoku
    round: int                                    = 0
    honba: int                                    = 0
    num_players: int                              = 0
    final_draw: int                               = 0
    final_discard: int                            = 0
    tiles_in_wall: int                            = 0
    is_final_round: bool                          = False

    # Events describing what happened in this kyoku
    # Each event is of the form (seat, event type, *event data)
    # e.g. (2, "draw", 34) means original West seat drew 4 sou
    events: List[Event]                           = field(default_factory=list)

    # Index of the final "draw" and "discard" events for each player
    # Used to check if a given event is a player's last draw/discard
    final_draw_event_index: List[int]             = field(default_factory=list)
    final_discard_event_index: List[int]          = field(default_factory=list)

    # doras include the round doras AND the red fives; there can be multiple of the same dora tile
    doras: List[int]                              = field(default_factory=list)
    uras: List[int]                               = field(default_factory=list)

    # The result of the kyoku in the format (type, result object(s))
    # either ("ron", Ron(...), ...) for a (double, triple) ron
    #     or ("tsumo", Tsumo(...)) for a tsumo
    #     or ("draw", Draw(...)) for a draw
    result: Tuple[Any, ...]                       = ()

    # for each player, we keep track of the current state of that player
    # this is represented by several lists indexed by seat, below
    # `hands` keeps track of hand, calls, shanten
    hands: List[Hand]                             = field(default_factory=list)
    # `pond` keeps track of all discards so far
    pond: List[List[int]]                         = field(default_factory=list)
    # `furiten` keeps track of whether a player is in furiten
    furiten: List[bool]                           = field(default_factory=list)

    # we also keep track of some facts for each player
    # store the scores of each player at the beginning of the kyoku
    start_scores: List[int]                       = field(default_factory=list)
    # store the starting hand of each player
    haipai: List[Hand]                            = field(default_factory=list)
    # store each player's ukeire count at the start and the end of a round (if tenpai)
    # -1 if the player is not tenpai
    haipai_ukeire: List[int]                      = field(default_factory=list)
    final_ukeire: List[int]                       = field(default_factory=list)

@dataclass
class GameMetadata:
    """Facts that apply across every kyoku"""
    num_players: int
    name: List[str]                  # name of each player indexed by seat
    game_score: List[int]            # final scores (points) indexed by seat
    final_score: List[int]           # final scores (points plus uma) indexed by seat
    # the fields below are equivalent to Kyoku.doras/uras, and only here for technical reasons
    # (they are parsed first from the raw log, and then used to populate Kyoku)
    dora_indicators: List[List[int]] # lists of dora indicators, one for each kyoku
    ura_indicators: List[List[int]]  # lists of ura indicators, one for each kyoku
    use_red_fives: bool              # whether the game uses red fives (only checks tenhou right now)
