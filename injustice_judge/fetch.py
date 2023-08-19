import functools
import google.protobuf as pb  # type: ignore[import]
import proto.liqi_combined_pb2 as proto
from google.protobuf.message import Message  # type: ignore[import]
from typing import *
from .constants import Kyoku, DORA_INDICATOR, LIMIT_HANDS, YAKU_NAMES
from .utils import remove_red_five, sorted_hand
from .shanten import calculate_shanten, calculate_ukeire

###
### parsing-related types
###

TenhouLog = List[List[Any]]
MajsoulLog = Tuple[Message, bytes]

###
### loading and parsing mahjong soul games
###

class MahjongSoulAPI:
    """Helper class to interface with the Mahjong Soul API"""
    async def __aenter__(self):
        import websockets
        self.ws = await websockets.connect("wss://mjusgs.mahjongsoul.com:9663/")
        self.ix = 0
        return self
    async def __aexit__(self, err_type, err_value, traceback):
        await self.ws.close()

    async def call(self, name, **fields: Dict[str, Any]) -> Message:
        method = next((svc.FindMethodByName(name) for svc in proto.DESCRIPTOR.services_by_name.values() if name in [method.name for method in svc.methods]), None)
        assert method is not None, f"couldn't find method {name}"

        req: Message = pb.reflection.MakeClass(method.input_type)(**fields)
        res: Message = pb.reflection.MakeClass(method.output_type)()

        tx: bytes = b'\x02' + self.ix.to_bytes(2, "little") + proto.Wrapper(name=f".{method.full_name}", data=req.SerializeToString()).SerializeToString()  # type: ignore[attr-defined]
        await self.ws.send(tx)
        rx: bytes = await self.ws.recv()
        assert rx[0] == 3, f"Expected response message, got message of type {rx[0]}"
        assert self.ix == int.from_bytes(rx[1:3], "little"), f"Expected response index {self.ix}, got index {int.from_bytes(rx[1:3], 'little')}"
        self.ix += 1

        wrapper: Message = proto.Wrapper()  # type: ignore[attr-defined]
        wrapper.ParseFromString(rx[3:])
        res.ParseFromString(wrapper.data)
        assert not res.error.code, f"{method.full_name} request recieved error {res.error.code}"
        return res

async def fetch_majsoul(link: str) -> Tuple[MajsoulLog, int]:
    """
    Fetch a raw majsoul log from a given link, returning a parsed log and the specified player's seat
    Example link: https://mahjongsoul.game.yo-star.com/?paipu=230814-90607dc4-3bfd-4241-a1dc-2c639b630db3_a878761203

    """
    assert link.startswith("https://mahjongsoul.game.yo-star.com/?paipu="), "expected mahjong soul link starting with https://mahjongsoul.game.yo-star.com/?paipu="
    if not "_a" in link:
        print("Assuming you're the first east player, since mahjong soul link did not end with _a<number>")

    identifier, *player_string = link.split("https://mahjongsoul.game.yo-star.com/?paipu=")[1].split("_a")
    ms_account_id = None if len(player_string) == 0 else int((((int(player_string[0])-1358437)^86216345)-1117113)/7)
    try:
        f = open(f"cached_games/game-{identifier}.json", 'rb')
        record = proto.ResGameRecord()  # type: ignore[attr-defined]
        record.ParseFromString(f.read())
        data = record.data
        return (record.head, record.data), next((acc.seat for acc in record.head.accounts if acc.account_id == ms_account_id), 0)
    except Exception as e:
        import os
        import dotenv
        import requests
        import uuid
        env_path = os.path.join(os.path.dirname(__file__), "config.env")
        dotenv.load_dotenv("config.env")
        UID = os.environ.get("ms_uid")
        TOKEN = os.environ.get("ms_token")
        MS_VERSION = requests.get(url="https://mahjongsoul.game.yo-star.com/version.json").json()["version"][:-2]

        async with MahjongSoulAPI() as api:
            print("Calling heatbeat...")
            await api.call("heatbeat")
            print("Requesting initial access token...")
            USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/110.0"
            access_token = requests.post(url="https://passport.mahjongsoul.com/user/login", headers={"User-Agent": USER_AGENT, "Referer": "https://mahjongsoul.game.yo-star.com/"}, data={"uid":UID,"token":TOKEN,"deviceId":f"web|{UID}"}).json()["accessToken"]
            print("Requesting oauth access token...")
            oauth_token = (await api.call("oauth2Auth", type=7, code=access_token, uid=UID, client_version_string=f"web-{MS_VERSION}")).access_token
            print("Calling heatbeat...")
            await api.call("heatbeat")
            print("Calling oauth2Check...")
            assert (await api.call("oauth2Check", type=7, access_token=oauth_token)).has_account, "couldn't find account with oauth2Check"
            print("Calling oauth2Login...")
            client_device_info = {"platform": "pc", "hardware": "pc", "os": "mac", "is_browser": True, "software": "Firefox", "sale_platform": "web"}
            await api.call("oauth2Login", type=7, access_token=oauth_token, reconnect=False, device=client_device_info, random_key=str(uuid.uuid1()), client_version={"resource": f"{MS_VERSION}.w"}, currency_platforms=[], client_version_string=f"web-{MS_VERSION}", tag="en")
            print("Calling fetchGameRecord...")
            res3 = await api.call("fetchGameRecord", game_uuid=identifier, client_version_string=f"web-{MS_VERSION}")

        if not os.path.isdir("cached_games"):
            os.mkdir("cached_games")
        with open(f"cached_games/game-{identifier}.json", "wb") as f2:
            f2.write(res3.SerializeToString())

        return (res3.head, res3.data), next((acc.seat for acc in res3.head.accounts if acc.account_id == ms_account_id), 0)

def parse_majsoul(log: MajsoulLog) -> List[Kyoku]:
    metadata, raw_actions = log
    kyokus: List[Kyoku] = []
    kyoku: Kyoku = {}
    visible_tiles: List[int] = []
    dora_indicators: List[int] = []
    shanten: List[Tuple[float, List[int]]] = []
    convert_tile = lambda tile: {"m": 51, "p": 52, "s": 53}[tile[1]] if tile[0] == "0" else {"m": 10, "p": 20, "s": 30, "z": 40}[tile[1]] + int(tile[0])
    majsoul_hand_to_tenhou = lambda hand: list(sorted_hand(map(convert_tile, hand)))
    last_seat = 0

    @functools.cache
    def parse_wrapped_bytes(data):
        wrapper = proto.Wrapper()
        wrapper.ParseFromString(data)
        name = wrapper.name.strip(f'.{proto.DESCRIPTOR.package}')
        try:
            msg = pb.reflection.MakeClass(proto.DESCRIPTOR.message_types_by_name[name])()
            msg.ParseFromString(wrapper.data)
        except KeyError as e:
            raise Exception(f"Failed to find message name {name}")
        return name, msg

    for name, action in [parse_wrapped_bytes(action.result) for action in parse_wrapped_bytes(raw_actions)[1].actions if len(action.result) > 0]:
        if name == "RecordNewRound":
            if "events" in kyoku:
                kyokus.append(kyoku)
            haipais = [action.tiles0, action.tiles1, action.tiles2]
            num_players = 3
            if len(action.tiles3) > 0:
                haipais.append(action.tiles3)
                num_players = 4
            kyoku = {
                "round": action.chang*4 + action.ju,
                "honba": action.ben,
                "num_players": num_players,
                "events": [],
                "result": None,
                "hands": list(map(majsoul_hand_to_tenhou, haipais)),
                "final_waits": None,
                "final_ukeire": None
            }
            dora_indicators = [DORA_INDICATOR[convert_tile(dora)] for dora in action.doras]
            visible_tiles = []
            first_tile: int = kyoku["hands"][action.ju].pop() # dealer starts with 14, remove the last tile so we can calculate shanten
            shanten = list(map(calculate_shanten, kyoku["hands"]))
            for t in range(num_players):
                kyoku["events"].append((t, "haipai", sorted_hand(kyoku["hands"][t])))
                kyoku["events"].append((t, "shanten", shanten[t]))
            # pretend we drew the first tile
            kyoku["events"].append((action.ju, "draw", first_tile))
            kyoku["hands"][action.ju].append(first_tile)
        elif name == "RecordDealTile":
            tile = convert_tile(action.tile)
            kyoku["events"].append((action.seat, "draw", tile))
            kyoku["hands"][action.seat].append(tile)
        elif name == "RecordDiscardTile":
            tile = convert_tile(action.tile)
            hand = kyoku["hands"][action.seat]
            kyoku["events"].append((action.seat, "discard", tile))
            if action.is_liqi:
                kyoku["events"].append((action.seat, "riichi", tile))
            hand.remove(tile)
            visible_tiles.append(tile)
            dora_indicators = [DORA_INDICATOR[convert_tile(dora)] for dora in action.doras]
            new_shanten = calculate_shanten(hand)
            if new_shanten != shanten[action.seat]:
                kyoku["events"].append((action.seat, "shanten_change", shanten[action.seat], new_shanten))
                shanten[action.seat] = new_shanten
            # check if the resulting hand is tenpai
            if new_shanten[0] == 0:
                ukeire = calculate_ukeire(hand, visible_tiles + dora_indicators)
                potential_waits = new_shanten[1]
                kyoku["events"].append((action.seat, "tenpai", sorted_hand(hand), potential_waits, ukeire))
        elif name == "RecordChiPengGang":
            tile = convert_tile(action.tiles[-1])
            if len(action.tiles) == 4:
                kyoku["events"].append((action.seat, "minkan", tile))
                kyoku["hands"][action.seat].remove(tile) # remove the extra tile from hand
            elif action.tiles[0] == action.tiles[1]:
                kyoku["events"].append((action.seat, "pon", tile))
            else:
                kyoku["events"].append((action.seat, "chii", tile))
            kyoku["hands"][action.seat].append(tile)
        elif name == "RecordAnGangAddGang":
            tile = convert_tile(action.tiles)
            kyoku["events"].append((action.seat, "ankan", tile))
            kyoku["hands"][action.seat].remove(tile)
            visible_tiles.append(tile)
            dora_indicators = [DORA_INDICATOR[convert_tile(dora)] for dora in action.doras]
        elif name == "RecordHule":
            if len(action.hules) > 1:
                print("don't know how tenhou represents multi ron")
            h = action.hules[0]
            han = sum(fan.val for fan in h.fans)
            score_string = f"{h.fu}符{han}飜"
            point_string = f"{h.point_rong}点"
            if h.point_rong >= 8000:
                assert han in LIMIT_HANDS, f"limit hand with {han} han is not in LIMIT_HANDS"
                score_string = LIMIT_HANDS[han]
            if h.zimo:
                point_string = f"{h.point_zimo_xian}-{h.point_zimo_qin}点"
                kyoku["hands"][h.seat].pop() # remove that tile so we can calculate waits/ukeire
            yakus = [name for _, name in sorted((fan.id, f"{YAKU_NAMES[fan.id]}({fan.val}飜)") for fan in h.fans)]
            kyoku["result"] = ["和了", list(action.delta_scores), [h.seat, last_seat, h.seat, score_string+point_string, *yakus]]
            kyoku["final_waits"] = [w for _, w in shanten]
            kyoku["final_ukeire"] = [calculate_ukeire(h, visible_tiles + dora_indicators) for h in kyoku["hands"]]
        elif name == "RecordNoTile":
            kyoku["result"] = ["流局", *(score_info.delta_scores for score_info in action.scores)]
            kyoku["final_waits"] = [w for _, w in shanten]
            kyoku["final_ukeire"] = [calculate_ukeire(h, visible_tiles + dora_indicators) for h in kyoku["hands"]]
        elif name == "RecordBaBei": # kita
            hand = kyoku["hands"][action.seat]
            kyoku["events"].append((action.seat, "kita"))
            kyoku["hands"][action.seat].remove(44)
            visible_tiles.append(44)
        else:
            print("unhandled action:", name, action)
        if hasattr(action, "seat"):
            last_seat = action.seat
    kyokus.append(kyoku)
    return kyokus

###
### loading and parsing tenhou games
###

def fetch_tenhou(link: str) -> Tuple[TenhouLog, int]:
    """
    Fetch a raw tenhou log from a given link, returning a parsed log and the specified player's seat
    Example link: https://tenhou.net/0/?log=2023072712gm-0089-0000-eff781e1&tw=1
    """
    import json
    assert link.startswith("https://tenhou.net/0/?log="), "expected tenhou link starting with https://tenhou.net/0/?log="
    if not link[:-1].endswith("&tw="):
        print("Assuming you're the first east player, since tenhou link did not end with ?tw=<number>")

    identifier = link.split("https://tenhou.net/0/?log=")[1].split("&")[0]
    player = int(link[-1])
    try:
        f = open(f"cached_games/game-{identifier}.json", 'r')
        return json.load(f)["log"], player
    except Exception as e:
        import requests
        import os
        USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/110.0"
        url = f"https://tenhou.net/5/mjlog2json.cgi?{identifier}"
        print(f" Fetching game log at url {url}")
        r = requests.get(url=url, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        log = r.json()
        if not os.path.isdir("cached_games"):
            os.mkdir("cached_games")
        with open(f"cached_games/game-{identifier}.json", "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False)
        return log["log"], player

def parse_tenhou(raw_kyoku: TenhouLog) -> Kyoku:
    [
        [current_round, current_honba, num_riichis],
        scores,
        doras,
        uras,
        haipai0,
        draws0,
        discards0,
        haipai1,
        draws1,
        discards1,
        haipai2,
        draws2,
        discards2,
        haipai3,
        draws3,
        discards3,
        result
    ] = raw_kyoku
    hand = cast(List[List[int]], [haipai0, haipai1, haipai2, haipai3])
    draws = cast(List[List[Union[int, str]]], [draws0, draws1, draws2, draws3])
    discards = cast(List[List[Union[int, str]]], [discards0, discards1, discards2, discards3])
    dora_indicators = cast(List[int], list(map(DORA_INDICATOR.get, doras)))

    # get a sequence of events based on discards only
    turn = current_round
    if current_round >= 4:
        turn -= 4
    last_turn: int = 0
    last_discard: int = 0
    num_players = 4
    i = [0] * num_players
    visible_tiles = []
    events: List[Any] = []
    gas = 1000
    num_dora = 1
    shanten = list(map(calculate_shanten, hand))
    for t in range(num_players):
        events.append((t, "haipai", sorted_hand(hand[t])))
        events.append((t, "shanten", shanten[t]))

    @functools.cache
    def get_call_name(draw: str) -> str:
        """Get the type of a call from tenhou's log format, e.g. "12p1212" -> "pon" """
        ret = "chii"   if "c" in draw else \
              "riichi" if "r" in draw else \
              "pon"    if "p" in draw else \
              "kakan"  if "k" in draw else \
              "ankan"  if "a" in draw else \
              "minkan" if "m" in draw else "" # minkan = daiminkan, but we want it to start with "m"
        assert ret != "", f"couldn't figure out call name of {draw}"
        return ret

    @functools.cache
    def extract_call_tile(draw: str) -> int:
        call_type = get_call_name(draw)
        # the position of the letter determines where it's called from
        # but we don't use this information, we just brute force check for calls
        if draw[0] == call_type[0]: # from kamicha
            return int(draw[1:3])
        elif draw[2] == call_type[0]: # from toimen
            return int(draw[3:5])
        elif draw[4] == call_type[0]: # from shimocha
            return int(draw[5:7])
        elif len(draw) > 7 and draw[6] == call_type[0]: # from shimocha
            return int(draw[7:9])
        assert False, f"failed to extract {call_type} tile from {draw}"

    while gas >= 0:
        gas -= 1
        if i[turn] >= len(discards[turn]):
            break

        # pon / kan handling
        # we have to look at the next draw of every player first
        # if any of them pons or kans the previously discarded tile, control goes to them
        turn_changed = False
        if last_discard != 0:
            for t in range(num_players):
                if turn != t and i[t] < len(draws[t]):
                    draw = draws[t][i[t]]
                    if type(draw) is str:
                        called_tile = extract_call_tile(draw)
                        if remove_red_five(called_tile) == remove_red_five(last_discard):
                            turn = t
                            turn_changed = True
                            break
        if turn_changed:
            last_discard = 0
            continue

        # first handle the draw
        # could be a regular draw, chii, pon, or daiminkan
        # then handle the discard
        # could be a regular discard, riichi, kakan, or ankan

        def handle_call(call: str):
            """called every time a call happens"""
            call_type = get_call_name(call)
            called_tile = extract_call_tile(call)
            events.append((turn, call_type, called_tile))
            nonlocal num_dora
            if call_type in ["minkan", "ankan", "kakan"]:
                num_dora += 1
                visible_tiles.append(called_tile) # account for visible kan tile
            return called_tile

        draw = draws[turn][i[turn]]
        if type(draw) is str:
            hand[turn].append(handle_call(draw))
            draw = hand[turn][-1]
        else:
            assert type(draw) == int, f"failed to handle unknown draw type: {draw}"
            hand[turn].append(draw)
            events.append((turn, "draw", draw))

        discard = discards[turn][i[turn]]
        if type(discard) is str:
            last_discard = handle_call(discard)
            last_discard = last_discard if last_discard != 60 else draw # 60 = tsumogiri
            hand[turn].remove(last_discard)
            visible_tiles.append(last_discard)
        elif discard == 0: # the draw earlier was daiminkan, so no discard happened
            pass
        else:
            assert type(discard) == int, f"failed to handle unknown discard type: {discard}"
            last_discard = discard if discard != 60 else draw # 60 = tsumogiri
            hand[turn].remove(last_discard)
            visible_tiles.append(last_discard)
            events.append((turn, "discard", last_discard))

        new_shanten = calculate_shanten(hand[turn])
        if new_shanten != shanten[turn]: # compare both the shanten number and the iishanten group
            events.append((turn, "shanten_change", shanten[turn], new_shanten))
            shanten[turn] = new_shanten

        i[turn] += 1 # done processing this draw/discard

        # check if the resulting hand is tenpai
        if new_shanten[0] == 0:
            ukeire = calculate_ukeire(hand[turn], visible_tiles + dora_indicators[:num_dora])
            potential_waits = new_shanten[1]
            events.append((turn, "tenpai", sorted_hand(hand[turn]), potential_waits, ukeire))

        # change turn to next player
        turn += 1
        if turn == num_players:
            turn = 0
    assert gas >= 0, "ran out of gas"
    assert len(dora_indicators) == num_dora, "there's a bug in counting dora"

    # get waits of final hands
    final_waits = [w for _, w in shanten]
    final_ukeire = [calculate_ukeire(h, visible_tiles + dora_indicators) for h in hand]

    dora_indicators[:num_dora]
    return {
        "round": current_round,
        "honba": current_honba,
        "num_players": num_players,
        "events": events,
        "result": result,
        "hands": hand,
        "final_waits": final_waits,
        "final_ukeire": final_ukeire,
    }