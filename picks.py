import re
import pandas as pd
import requests
import unidecode  # This helps remove accents for easier name matching

# File paths
GAME_STAT = True
odds_file = 'picks.txt'
output_file = 'picks_ev.txt'
excel_file = '5reb'
token = "wLpQ9z63dGG1WblD"

if GAME_STAT:
    input_excel = f'./rank/game_{excel_file}.xlsx'
else:
    input_excel = f'./rank/{excel_file}.xlsx'

custom_prob = False
stat_dict = {1:'pt', 2:'ast', 3:'reb', 21:'3pt'}

if 'pt' in input_excel:
    statType = 1
    if GAME_STAT:
        value = input_excel[12:-7]
    else:
        value = input_excel[7:-7]
elif 'ast' in input_excel:
    statType = 2
    if GAME_STAT:
        value = input_excel[12:-8]
    else:
        value = input_excel[7:-8]
elif 'reb' in input_excel:
    statType = 3
    if GAME_STAT:
        value = input_excel[12:-8]
    else:
        value = input_excel[7:-8]
elif '_3' in input_excel:
    statType = 21
    if GAME_STAT:
        value = input_excel[12:-7]
    else:
        value = input_excel[7:-7]

# Define functions for calculating earning and implied probability
def calculate_earning(rank):
    if (rank > 20):
        return 200 + (rank-20)
    return min(rank * 10, 200)

def implied_probability(odds):
    if odds > 0:
        return 100 / (odds + 100)
    else:
        return abs(odds) / (abs(odds) + 100)
    
def probability_to_odds(probability):
    if probability > 0.5:
        if probability == 1:
            odds = -8000
        else:
            # For negative odds (implying a probability over 50%)
            odds = -100 * (probability / (1 - probability))
    else:
        if probability == 0:
            odds = 8000
        else:
            # For positive odds (implying a probability under 50%)
            odds = 100 * ((1 - probability) / probability)
    
    return odds

def should_skip_line(line: str) -> bool:
    s = line.strip()
    return (not s) or s.upper().startswith("PROB") or s.upper().startswith("QUES") or s.upper().startswith("DOUBT")

# Function to get player ID by name
def get_player_id(player_name):
    norm = normalize_name(player_name)
    match = player_info_df[player_info_df['Name_norm'] == norm]
    return match['id'].values[0] if not match.empty else None

def ensure_player_entry(player_name, default_rank=999.0):
    """Ensure the (normalized) player exists in player_info with default fields.
    Returns the normalized name used as the key."""
    norm_name = normalize_name(player_name)
    if norm_name not in player_info:
        player_info[norm_name] = {
            'rank': float(default_rank),
            'votes': 0.0,
            'rankMomentum': 0.0,
            'votesMomentum': 0.0
        }
    return norm_name

# Define headers for the request (remove pseudo headers)
headers = {
    "accept": "application/json",
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "cache-control": "no-cache",
    "content-type": "application/json",
    "origin": "https://www.realsports.io",
    "pragma": "no-cache",
    "real-auth-info": "jvbzP76v!e3OdkxzE!5022ac70-75d7-44ef-b24b-dc47af7bc8d2",
    "real-device-name": "5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "real-device-type": "desktop_web",
    "real-device-uuid": "4311295d-c470-419d-a2b3-bfe30c3565ca",
    "real-request-token": token,
    "real-version": "20",
    "referer": "https://www.realsports.io/",
    "sec-ch-ua": "\"Chromium\";v=\"130\", \"Google Chrome\";v=\"130\", \"Not?A_Brand\";v=\"99\"",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": "\"Windows\"",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
}

def normalize_name(name: str) -> str:
    """Normalize player names for matching:
    - remove accents
    - strip 'Jr.' suffix (with or without comma)
    """
    if not isinstance(name, str):
        return ''
    name = unidecode.unidecode(name)
    # Remove optional comma + space + Jr / Jr. at the end of the string
    name = re.sub(r',?\s+Jr\.?$', '', name, flags=re.IGNORECASE)
    return name.strip()

# Function to fetch player stat using the API
def fetch_player_stat(player_id, statType, value):
    url = f"https://web.realsports.io/getplayerboxscoresplits?entityId={player_id}&entityType=player&sport=nba&statType={statType}&value={value}"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    data = response.json()
    overall_split = next((split for split in data.get("splits", []) if split.get("type") == "Overall" and split.get("numGames") == 5), None)
    return overall_split.get("pctTimesOver") if overall_split else 0

# Step 1: Read rank and votes data from Excel
rank_data = pd.read_excel(input_excel)
# Load player IDs and names from CSV into a DataFrame
player_info_df = pd.read_csv("real_id.csv")  # Load "real_id.csv" containing player ID, Name
player_info_df['Name_norm'] = player_info_df['Name'].apply(normalize_name)

# Create a dictionary to store rank and votes for each player
player_info = {}
for _, row in rank_data.iterrows():
    name = normalize_name(row['displayName'])
    rank = row['rank']
    votes = row['votes']
    try:
        rank_momentum = row['rankMomentum']
        votes_momentum = row['votesMomentum']
        player_info[name] = {
            'rank': rank,
            'votes': votes,
            'rankMomentum': rank_momentum,
            'votesMomentum': votes_momentum
        }
    except:
        player_info[name] = {
            'rank': rank,
            'votes': votes,
            'rankMomentum': 0,
            'votesMomentum': 0
        }

# Step 2: Read player names and odds from the text file
players_with_odds = []
players_with_odds_name = []
with open(odds_file, 'r') as file:
    lines = file.readlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Skip empty / PROB / QUES lines
        if should_skip_line(line):
            i += 1
            continue

        if "playerlist" in line:
            i += 1
            while i < len(lines):
                raw = lines[i].strip()

                # Stop if we hit another section keyword (optional, depending on file format)
                if "playerlist" in raw:
                    break

                # Skip empty / PROB / QUES lines inside playerlist
                if should_skip_line(raw):
                    i += 1
                    continue

                if custom_prob:
                    player_name_raw, p = raw.split(',')
                    odds = probability_to_odds(float(p))
                else:
                    player_name_raw = raw
                    odds = 0

                norm_name = normalize_name(player_name_raw)

                # Try API-based probability if we have an ID and we haven't processed this player yet
                player_id = get_player_id(norm_name)
                if player_id and norm_name in player_info and norm_name not in players_with_odds_name:
                    try:
                        pct_times_over = float(fetch_player_stat(player_id, statType, value)) / 100.0
                        odds = probability_to_odds(pct_times_over)
                        players_with_odds.append((norm_name, odds, ''))
                        players_with_odds_name.append(norm_name)
                        print(f"Fetch player prob from reals: {norm_name} {pct_times_over}")
                    except requests.RequestException as e:
                        print(f"Error fetching data for {norm_name} (ID: {player_id}): {e}")
                else:
                    print(f"Cannot found {norm_name}, id: {player_id}")
                i += 1

            # continue outer while after finishing playerlist block
            continue

        if i >= len(lines):
            break

        # Skip any empty/PROB/QUES lines before reading a player name
        while i < len(lines) and should_skip_line(lines[i]):
            i += 1
        if i >= len(lines):
            break

        player_name = lines[i].strip()  # Player's name
        i += 1  # Move to line with odds

        # Skip any empty/PROB/QUES lines before reading odds
        while i < len(lines) and should_skip_line(lines[i]):
            i += 1
        if i >= len(lines):
            break

        odds_str = lines[i].strip()
        i += 1

        # Convert odds to integer if valid
        try:
            odds = int(odds_str)
            norm_name = ensure_player_entry(player_name)  # add with defaults if missing

            # Record the odds (flag '*' = odds came from text file)
            if norm_name not in players_with_odds_name:
                players_with_odds.append((norm_name, odds, '*'))
                players_with_odds_name.append(norm_name)
        except ValueError:
            print(f"Skipping invalid odds for player: {player_name}")

        # Move to the next line
        i += 1


# Step 3: Calculate rank by sorting players based on rank and votes
# Sort by votes to assign rank positions

players_sorted_by_votes = sorted(players_with_odds, key=lambda x: player_info[x[0]]['votes'], reverse=True)

for idx, (name, odds, flag) in enumerate(players_sorted_by_votes, start=1):
    try:
        player_info[name]['votes_rank'] = idx
    except KeyError:
        print(f"KeyError: {name}")
    

# Sort by rank to assign rank positions
players_sorted_by_rank = sorted(players_with_odds, key=lambda x: player_info[x[0]]['rank'])
for idx, (name, odds, flag) in enumerate(players_sorted_by_rank, start=1):
    try:
        player_info[name]['rank_position'] = idx
    except KeyError:
        print(f"KeyError: {name}")

# Calculate adjusted rank and process each player
players = []
for name, odds, source_flag in players_with_odds:
    try:
        votes_rank = player_info[name].get('votes_rank', 0)
        rank_position = player_info[name].get('rank_position', 0)
        adjusted_rank = votes_rank * 0.2 + rank_position * 0.8
        earning = calculate_earning(adjusted_rank)
        
        if votes_rank and rank_position:
            probability = implied_probability(odds)
            ev = earning * probability
            players.append((name, player_info[name]['rank'], player_info[name]['votes'], earning, probability, ev, source_flag))
    except KeyError:
        print(f"KeyError: {name}")


# Step 4: Sort players by EV in descending order
players.sort(key=lambda x: x[1], reverse=True)
players.sort(key=lambda x: x[5], reverse=True)

# Step 4: Write sorted results to output file
with open(output_file, 'w', encoding="utf-8") as file:
    file.write(f"{stat_dict[statType]} {value}\n")
    file.write(f"{'Rank':<5} {'Name':<25} {'Votes':<10} {'Prob':<6} {'EV':<6}\n")
    file.write("====================================\n")
    for name, rank, votes, earning, probability, ev, source_flag in players:
        # Round values to two decimal places and format the output
        display_rank = f"{rank:.2f}"
        display_votes = f"{votes:.2f}"
        display_prob = f"{probability:.2f}"
        display_ev = f"{ev:.2f}"
        
        # Format the momentum values, rounding and limiting to one decimal if needed
        rank_momentum = f"{player_info[name]['rankMomentum']:+.1f}"
        votes_momentum = f"{player_info[name]['votesMomentum']:+.1f}"
        
        file.write(f"{display_rank}({rank_momentum}) {name:<25} {source_flag} {display_votes}({votes_momentum}) {display_prob} {display_ev}\n")

    file.write("\nSorted by Rank:\n")
    file.write(f"{'Name':<25} {'Rank':<10}\n")
    file.write("====================================\n")
    players.sort(key=lambda x: x[1])  # Sort by rank
    for name, rank, votes, earning, probability, ev, source_flag in players:
        display_rank = f"{rank:.2f}"
        file.write(f"{name:<25} {display_rank:<10}\n")

print(f"Sorted EV results have been saved to {output_file}")