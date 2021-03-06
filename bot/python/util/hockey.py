import datetime
import enum
import requests

import util.web

DETAIL_HELP_TEXT = "stats|link|lastgame|schedule !season=int space separated search terms"

SEARCH_BASE_URL = "http://www.hockey-reference.com/search/search.fcgi?search="
BASE_RESULT_URL = "http://www.hockey-reference.com"


class HockeyErrorCode(enum.Enum):
    No_Search_Terms = 0
    Bad_Season_Format = 1
    Unknown_Command = 2
    No_Format = 3
    No_Such_Thing = 4

class HockeyError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message

class SearchType(enum.Enum):
    Any = ""
    Franchise = "franchise"
    Player = "player"

ALL_TYPES = [st.value for st in SearchType]

def get_search_type(name):
    name = name.lower()
    if name in ["team", "franchise"]:
        return SearchType.Franchise
    elif name == "player":
        return SearchType.Player
    else:
        raise ValueError("unknown type %s" % name)

def __get_name_type_and_url(url):
    dom, url = util.web.get_dom_from_url(url, allow_redirects=True)

    if "players" in url:
        result_type = SearchType.Player
    elif "teams" in url:
        result_type = SearchType.Franchise
    else:
        raise HockeyError(HockeyErrorCode.No_Such_Thing, "no such thing")

    if result_type == SearchType.Franchise:
        name = dom.find_class("teams")[0][0][1][0][0]
    elif result_type == SearchType.Player:
        try:
            name = dom.find_class("players")[0][0][2][0]
        except IndexError:
            # maybe a legacy player
            name = dom.find_class("players")[0][0][0][0]
    else:
        raise RuntimeError("unsupported type %s" % result_type)

    return name.text.strip(), result_type, url

def search(search_type, terms, result_limit):
    if isinstance(search_type, str):
        search_type = get_search_type(search_type)

    if not isinstance(terms, list):
        raise ValueError("terms must be a list")

    url = SEARCH_BASE_URL + "+".join(terms)

    results = {
        SearchType.Franchise : list(),
        SearchType.Player : list(),
    }

    try:
        dom = util.web.get_dom_from_url(url)
    except util.web.RedirectError:
        # redirect means only a single result
        name, type, url = __get_name_type_and_url(url)
        results[SearchType.Player].append({
            "url" : url,
            "type" : type,
            "name" : name,
        })
       
        return results

    search_results = dom.find_class("search-item")

    result_counter = 0

    for result in search_results:
        result_counter += 1
        next_result = dict()

        try:
            result_type = get_search_type(result.getparent().attrib['id'][:-1]) # lose trailing s
        except ValueError:
            # unknown result type
            continue

        next_result["type"] = result_type
        
        if search_type != SearchType.Any:
            if result_type != search_type:
                continue

        for child in result:

            attribute = child.attrib["class"].split("-")[-1]

            if attribute == "name":
                try:
                    link = child[0][0].attrib["href"]
                    value = child[0][0].text
                except IndexError: 
                    link = child[0].attrib["href"]
                    value = child[0].text

            elif attribute == "url":
                value = (BASE_RESULT_URL + child.text)
            else:
                value = child.text

            next_result[attribute] = value

        if result_type == "player":
            try:
                next_result["team"] = next_result["team"].replace("Last Played for the ","")
            except KeyError:
                next_result["team"] = "N/A"

        # extract active years from name
        parts = next_result["name"].split()
        years_active = parts[-1][1:-1]
        name = " ".join(parts[:-1])
        next_result["name"] = name
        next_result["years-active"] = years_active

        results[result_type].append(next_result)

        if result_counter >= result_limit:
            break

    return results

ATTRIBUTE_MAP = {
    "losses_ot" : "otl",
    "rank_team" : "seed",
    "rank_team_playoffs" : "playoffs",
    "points_pct" : "points",
    "faceoff_percentage" : "FO%",
    "games_played" : "GP",
    "giveaways" : "GV",
    "goals" : "G",
    "hits" : "HIT",
    "pen_min" : "PIM",
    "plus_minus" : "+/-",
    "points" : "PTS",
    "shot_pct" : "S%",
    "shots" : "S",
    "takeaways" : "TK",
    "time_on_ice_avg" : "ATOI",
    "assists" : "A",
    "team_id" : "Tm",
}

TABLE_OFFSET = {
    SearchType.Franchise : 0,
    SearchType.Player : 1,
    
}

IGNORE_ATTRIBUTES = {
    SearchType.Franchise : [
        "lg_id",
        "coaches",
        "sos",
        "srs",
        "team_name",
    ],
    SearchType.Player : [
        "assists_ev",
        "assists_pp",
        "assists_sh",
        "award_summary",
        "faceoff_losses",
        "faceoff_wins",
        "goals_ev",
        "goals_gw",
        "goals_pp",
        "goals_sh",
        "shots_attempted",
        "time_on_ice",
    ]
}

def __extract_row(row, ignored_attributes):
    stats = dict()

    for col in row:

        attribute = col.attrib["data-stat"]

        if attribute in ignored_attributes:
            # don't care about these attributes
            continue
        elif attribute == "season":
            try:
                value = col[0].text
            except IndexError:
                value = col.text

            start, end = value.split("-")

            end = start[:2] + end

            value = int(end)
        else:

            try:
                raw_text = col[0].text
            except IndexError:
                raw_text = col.text

            try:
                value = int(raw_text)
            except ValueError:
                value = raw_text
            except TypeError:
                value = ""
                
        attribute = ATTRIBUTE_MAP.get(attribute, attribute)

        stats[attribute] = value

    return stats

def __get_current_season():
    now = datetime.datetime.now()

    if now.month < 9:
        return now.year
    else:
        return now.year + 1

def __get_stats(result_type, result, season):

    dom = util.web.get_dom_from_url(result["url"])

    try:
        stats_table = dom.find_class("table_outer_container")[TABLE_OFFSET[result_type]][0][0][3]
    except IndexError:
        # maybe a legacy player
        stats_table = dom.find_class("table_outer_container")[0][0][0][3]

    for row in stats_table:
        season_stats = __extract_row(row, IGNORE_ATTRIBUTES[result_type])
        season_stats["url"] = result["url"]
        season_stats["type"] = result_type
        season_stats["name"] = result["name"]
        if season_stats["season"] == season:
            return season_stats

    return season_stats    

def __select_result(results, result_limit):
    ret = None
    if len(results[SearchType.Franchise]) > 0:
        ret = results[SearchType.Franchise][:result_limit]
    else:
        ret = results[SearchType.Player][:result_limit]
    
    if result_limit == 1:
        return ret[0]
    else:
        return ret

def get_stats(terms, season):

    if not isinstance(terms, list):
        raise ValueError("terms must be a list")

    results = search(SearchType.Any, terms, result_limit=1)

    result = __select_result(results, 1)

    return __get_stats(result["type"], result, season)


def get_link(terms, result_limit):
    results = search(SearchType.Any, terms, result_limit)
    result = __select_result(results, result_limit)
    return result

STATS_FORMATS = {
    SearchType.Franchise : [
        "%(season)s %(name)s: GP:%(games)s, W:%(wins)s, L:%(losses)s, OL:%(otl)s, PTS:%(PTS)s, seed:%(seed)s %(playoffs)s",
    ],
    SearchType.Player : [
        "%(season)s %(name)s %(age)sy/o - %(Tm)s: GP:%(GP)s, G:%(G)s, A:%(A)s, PTS:%(PTS)s, +/-:%(+/-)s, S:%(S)s, FO%%:%(S%)s, PIM:%(PIM)s, ATOI:%(ATOI)smin, HIT:%(HIT)s",
        "%(season)s %(name)s %(age)sy/o - %(Tm)s: GP:%(games_goalie)s, GS:%(starts_goalie)s, W:%(wins_goalie)s, L:%(losses_goalie)s, OTL:%(ties_goalie)s, SV%%:%(save_pct)s, GAA:%(goals_against_avg)s, SO:%(shutouts)s",
        ],
}

def get_games(terms, season):
    results = search(SearchType.Any, terms, 1)
    result = __select_result(results, 1)
    
    if result["type"] == SearchType.Franchise:
        url = "%s%s_games.html" % (result["url"], season)
    else:
        url = result["url"]


    dom = util.web.get_dom_from_url(url)

    games_table = dom.find_class("stats_table")[0][3]

    played = list()
    upcoming = list()

    for r in games_table:
        row = __extract_row(r, [])

        row["name"] = result["name"]
        row["type"] = result["type"]
        row["url"] = result["url"]

        if result["type"] == SearchType.Player:
            row["game_outcome"] = row["game_result"]

        if row["game_location"] != "@":
            row["game_location"] = "vs"

        if row["game_outcome"] == "" :
            upcoming.append(row)
        else:
            outcome = row["game_outcome"]
            row["opp_outcome"] = "L" if outcome == "W" else "W"

            played.append(row)

    return played, upcoming

def __format_stats(stats, season):
    stats_type = stats["type"]

    for stats_format in STATS_FORMATS[stats_type]:
        try:
            return stats_format % stats
        except KeyError as e:
            continue

    return "no stats for %s in the %d season: %s" % (stats["name"], season, stats["url"])


def __format_link(result):
    return "%(name)s - %(url)s" % result

LASTGAME_FORMATS = {
    SearchType.Franchise : [
        "%(date_game)s -- %(name)s(%(game_outcome)s): G:%(G)s, S:%(S)s, PIM:%(PIM)s - %(game_location)s -  %(opp_name)s(%(opp_outcome)s): G:%(opp_goals)s, S:%(shots_against)s, PIM:%(pen_min_opp)s",
    ],
    SearchType.Player : [
        "%(date_game)s -- %(name)s - %(Tm)s(%(game_result)s) %(game_location)s %(opp_id)s - G:%(G)s, A:%(A)s, +/-:%(+/-)s, S:%(S)s, FO%%:%(S%)s, PIM:%(PIM)s, TOI:%(time_on_ice)smin, HIT:%(hits_all)s",
        "%(date_game)s -- %(name)s -  %(Tm)s(%(game_result)s) %(game_location)s %(opp_id)s - GA:%(goals_against)s, SA:%(shots_against)s, SV:%(saves)s, SV%%:%(save_pct)s",
        ],
}


def __format_last_game(game):
    lastgame_type = game["type"]

    for lastgame_format in LASTGAME_FORMATS[lastgame_type]:
        try:
            return lastgame_format % game
        except KeyError as e:
            continue

    return "no lastgame for %s: %s" % (game["name"], game["url"])

def __format_schedule(games):
    schedule_format = "%(date_game)s %(time_game)s %(game_location)s %(opp_name)s"

    prefix = "%(name)s: " % games[0]
    
    return prefix + ", ".join([schedule_format % game for game in games])

def execute_command(args, query):
    raw_season = args.get("season", __get_current_season())
    try:
        season = int(raw_season)
    except ValueError:
        raise HockeyError(HockeyErrorCode.Bad_Season_Format, "invalid season: %s" % raw_season)

    command = query[0].lower()
    if command == "help" or len(query) < 2:
        return DETAIL_HELP_TEXT
    
    terms = query[1:]

    try:
        if command == "stats":
            stats = get_stats(terms, season=season)
            return __format_stats(stats, season)
        elif command == "link":
            results = get_link(terms, 1)
            return __format_link(results)
        elif command == "lastgame":
            played_games, upcoming_games = get_games(terms, season)
            return __format_last_game(played_games[-1])
        elif command == "schedule":
            played_games, upcoming_games = get_games(terms, season)
            return __format_schedule(upcoming_games[:3])
    except HockeyError as e:
        return e.message

    raise HockeyError(HockeyErrorCode.Unknown_Command, "unknown command: %s" % command)

def __test():
    results = search("player", ["boston","bruins"], 1)
    assert len(results[SearchType.Franchise]) == 0, len(results[SearchType.Franchise])

    results = search("playER", ["boston","bruins"], 1)
    assert len(results[SearchType.Franchise]) == 0, len(results[SearchType.Franchise])

    results = search(SearchType.Player, ["boston","bruins"], 1)
    assert len(results[SearchType.Franchise]) == 0, len(results[SearchType.Franchise])

    results = search(SearchType.Franchise, ["boston","bruins"], 1)
    assert len(results[SearchType.Franchise]) == 1, len(results[SearchType.Franchise])

    results = search("team", ["boston","bruins"], 1)
    assert len(results[SearchType.Franchise]) == 1, len(results[SearchType.Franchise])

    results = search(SearchType.Player, ["seguin"], 99)
    assert len(results[SearchType.Player]) == 3, len(results[SearchType.Player])

    results = search(SearchType.Any, ["cal"], 1)
    assert len(results[SearchType.Franchise]) == 0, len(results[SearchType.Franchise])
    assert len(results[SearchType.Player]) == 1, len(results[SearchType.Player])

    stats = get_stats(["seguin"], 2017)
    assert stats["age"] == 25, stats["age"]

    stats = get_stats(["boston"], 2012)
    assert stats["losses"] == 29, stats["losses"]

    try:
        execute_command("stats !season=asdf".split())
        assert False, "should have thrown"
    except HockeyError as e:
        assert e.code == HockeyErrorCode.Bad_Season_Format, e.code

    try:
        execute_command("stats !season=2017".split())
        assert False, "should have thrown"
    except HockeyError as e:
        assert e.code == HockeyErrorCode.No_Search_Terms, e.code
        
    try:
        execute_command("foo !season=2017 boston".split())
        assert False, "should have thrown"
    except HockeyError as e:
        assert e.code == HockeyErrorCode.Unknown_Command, e.code

    text = execute_command("help".split())
    assert text == DETAIL_HELP_TEXT


    test_commands = [
        "stats bruins",
        "link bruins",
        "lastgame bruins",
        "schedule bruins",
        "stats seguin",
        "link seguin",
        "lastgame seguin",
        "stats horvat",
        "link horvat",
        "stats bo horvat",
        "link bo horvat",
        ]

    bad_commands = [
        "lastgame seguin",
        "schedule seguin",
        "lastgame bo horvat",
        "schedule bo horvat",
        "lastgame horvat",
        ]

    for tc in test_commands:
        print("executing:", tc)
        text = execute_command(tc.split())
        print("result:   ", text)

    print("probably good")

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        text = execute_command(sys.argv[1:])
        print(text)
    else:
        __test()
