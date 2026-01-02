import pandas as pd
import json
import subprocess

org_data = input()
# Parse the JSON string into a Python data structure
org_data = json.loads(org_data)
# Now data_list contains the list with the dictionary

module_data = {}

for item in org_data:
    tab = item["tab"]
    if tab not in module_data:
        module_data[tab] = {}
    module_data[tab] = item
    # print(tab, item['data'])


def vital():
    # Tendency
    vit_data = module_data["VITALS"]["data"]

    vit_data['BIRTHYEAR'] = int(vit_data['BIRTHYEAR']) + 1958

    # Extract the "data" field from the JSON

    # Convert the dictionary to a DataFrame
    df = pd.DataFrame([vit_data], dtype=str)
    print(df)

    # Convert the DataFrame back to JSON format
    vit_json = df.to_json(orient='records')

    return vit_json

def attribute():
    new_rating = input()
    # Split the data into a list of values
    new_rating_list = new_rating.split()

    # Create a DataFrame from the list
    new_df = pd.DataFrame([new_rating_list], columns=[
        'Layup',
        'STDunk', 'Dunk', 'Close', 'Mid', '3PT',
        'FT', 'PHook', 'PFade', 'PostC', 'Foul',
        'ShotIQ', 'Pass', 'Ball', 'SPD/BALL', 'Hands',
        'PassIQ', 'Vision', 'OCNST', 'ID',
        'PD', 'Steal', 'Block', 'OREB', 'DREB',
        'LQUI', 'HelpDIQ', 'PSPER', 'DCNST', 'INTNGBL', 'SPEED',	'ACCEL',	'STR',	'VERT',	'STAM',	'HSTL', '2KRating', 'inches'
    ])

    rtg_percent_columns = [
        'Layup',
        'STDunk', 'Dunk', 'Close', 'Mid', '3PT',
        'FT', 'PHook', 'PFade', 'PostC', 'Foul',
        'ShotIQ', 'Pass', 'Ball', 'SPD/BALL', 'Hands',
        'PassIQ', 'Vision', 'OCNST', 'ID',
        'PD', 'Steal', 'Block', 'OREB', 'DREB',
        'LQUI', 'HelpDIQ', 'PSPER', 'DCNST', 'INTNGBL', 'SPEED',	'ACCEL',	'STR',	'VERT',	'STAM',	'HSTL'
    ]

    new_df[rtg_percent_columns] = new_df[rtg_percent_columns].apply(
        lambda x: x.str.rstrip('%').astype(float))

    # Extract the "data" field from the JSON
    atr_data = module_data["ATTRIBUTES"]["data"]

    atr_data["MAX_OVR"] = int(float(atr_data["MAX_OVR"]))
    atr_data["DRIVING_LAYUP"] = int((new_df["Layup"][0]-25)*3)
    atr_data["POST_FADEAWAY"] = int((new_df["PFade"][0]-25)*3)
    atr_data["POST_HOOK"] = int((new_df["PHook"][0]-25)*3)
    atr_data["POST_CONTROL"] = int((new_df["PostC"][0]-25)*3)
    atr_data["DRAW_FOUL"] = int((new_df["Foul"][0]-25)*3)
    atr_data["SHOT_CLOSE"] = int((new_df["Close"][0]-25)*3)
    atr_data["MID-RANGE_SHOT"] = int((new_df["Mid"][0]-25)*3)
    atr_data["3PT_SHOT"] = int((new_df["3PT"][0]-25)*3)
    atr_data["FREE_THROW"] = int((new_df["FT"][0]-25)*3)
    atr_data["BALL_CONTROL"] = int((new_df["Ball"][0]-25)*3)
    atr_data["PASSING_IQ"] = int((new_df["PassIQ"][0]-25)*3)
    atr_data["PASSING_ACCURACY"] = int((new_df["Pass"][0]-25)*3)
    atr_data["OFFENSIVE_REBOUND"] = int((new_df["OREB"][0]-25)*3)
    atr_data["STANDING_DUNK"] = int((new_df["STDunk"][0]-25)*3)
    atr_data["DRIVING_DUNK"] = int((new_df["Dunk"][0]-25)*3)
    atr_data["SHOT_IQ"] = int((new_df["ShotIQ"][0]-25)*3)
    atr_data["PASSING_VISION"] = int((new_df["Vision"][0]-25)*3)
    atr_data["HANDS"] = int((new_df["Hands"][0]-25)*3)
    atr_data["DEFENSIVE_REBOUND"] = int((new_df["DREB"][0]-25)*3)
    atr_data["INTERIOR_DEFENSE"] = int((new_df["ID"][0]-25)*3)
    atr_data["PERIMETER_DEFENSE"] = int((new_df["PD"][0]-25)*3)
    atr_data["BLOCK"] = int((new_df["Block"][0]-25)*3)
    atr_data["STEAL"] = int((new_df["Steal"][0]-25)*3)
    atr_data["LATERAL_QUICKNESS"] = int((new_df["LQUI"][0]-25)*3)
    atr_data["SPEED_WITH_BALL"] = int((new_df["SPD/BALL"][0]-25)*3)
    atr_data["PASS_PERCEPTION"] = int((new_df["PSPER"][0]-25)*3)
    atr_data["DEFENSIVE_CONSISTENCY"] = int((new_df["DCNST"][0]-25)*3)
    atr_data["HELP_DEFENSIVE_IQ"] = int((new_df["HelpDIQ"][0]-25)*3)
    atr_data["OFFENSIVE_CONSISTENCY"] = int((new_df["OCNST"][0]-25)*3)
    atr_data["INTANGIBLES"] = int((new_df["INTNGBL"][0]-25)*3)
    atr_data["POTENTIAL"] = int(float(atr_data["POTENTIAL"]))
    atr_data["SPEED"] = int((new_df["SPEED"][0]-25)*3)
    atr_data["ACCELERATION"] = int((new_df["ACCEL"][0]-25)*3)
    atr_data["STRENGTH"] = int((new_df["STR"][0]-25)*3)
    atr_data["VERTICAL"] = int((new_df["VERT"][0]-25)*3)
    atr_data["STAMINA"] = int((new_df["STAM"][0]-25)*3)
    atr_data["HUSTLE"] = int((new_df["HSTL"][0]-25)*3)

    # Convert the dictionary to a DataFrame
    df = pd.DataFrame([atr_data], dtype=str)
    print(df)

    for a in ["SPEED", "ACCELERATION", "STRENGTH", "VERTICAL", "STAMINA", "HUSTLE"]:
        print(a, "\t", float(atr_data[a])/3+25)

    # Convert the DataFrame back to JSON format
    atr_json = df.to_json(orient='records')
    return atr_json


# ==================================================#
# Badges
def badges():

    bad_data = module_data["BADGES"]["data"]

    badges = input()
    # Split the data into a list of values
    badges_list = badges.split()

    # Create a DataFrame from the list
    columns = [
        'ACROBAT', 'AERIAL_WIZARD', 'BACKDOWN_PUNISHER', 'BULLY', 'DREAM_SHAKE',
        'DROP-STEPPER', 'FAST_TWITCH', 'FEARLESS_FINISHER', 'GIANT_SLAYER', 'LIMITLESS_TAKEOFF',
        'MASHER', 'POST_SPIN_TECHNICIAN', 'POSTERIZER', 'PRO_TOUCH', 'RISE_UP',
        'SLITHERY', 'AGENT_3', 'AMPED', 'BLINDERS', 'CATCH_SHOOT',
        'CLAYMORE', 'CLUTCH_SHOOTER', 'COMEBACK_KID', 'CORNER_SPECIALIST', 'DEADEYE',
        'GREEN_MACHINE', 'GUARD_UP', 'LIMITLESS_RANGE', 'MIDDY_MAGICIAN', 'SLIPPERY_OFF',
        'SPACE_CREATOR', 'VOLUME_SHOOTER', 'ANKLE_BREAKER', 'BAIL_OUT', 'BREAK_STARTER',
        'CLAMP_BREAKER', 'KILLER_COMBOS', 'DIMER', 'FLOOR_GENERAL', 'HANDLES_FOR_DAYS',
        'HYPERDRIVE', 'MISMATCH_EXPERT', 'NEEDLE_THREADER', 'POST_PLAYMAKER', 'QUICK_FIRST_STEP',
        'SPECIAL_DELIVERY', 'UNPLUCKABLE', 'VICE_GRIP', 'ANCHOR', 'ANKLE_BRACES',
        'CHALLENGER', 'CHASE_DOWN_ARTIST', 'CLAMPS', 'GLOVE', 'INTERCEPTOR',
        'MENACE', 'OFF-BALL_PEST', 'PICK_DODGER', 'POST_LOCKDOWN', 'POGO_STICK',
        'WORK_HORSE', 'BRICK_WALL', 'BOXOUT_BEAST', 'REBOUND_CHASER'
    ]

    for i, c in enumerate(columns):
        bad_data[c] = badges_list[i]

    # Extract the "data" field from the JSON

    # Convert the dictionary to a DataFrame
    df = pd.DataFrame([bad_data], dtype=str)
    print(df)

    # Convert the DataFrame back to JSON format
    bad_json = df.to_json(orient='records')

    return bad_json

#####################


def tendency():
    # Tendency
    tend_data = module_data["TENDENCIES"]["data"]

    tendency = input()
    # Split the data into a list of values
    tend_data_list = tendency.split()
    tend_data_list = [int(float(x.rstrip('%'))) for x in tend_data_list]

    # Create a DataFrame from the list
    columns = ['SHOT_UNDER_BASKET_TENDENCY', 'SHOT_CLOSE_TENDENCY', 'SHOT_MID-RANGE_TENDENCY', 'SHOT_THREE_TENDENCY', 'DRIVING_LAYUP_TENDENCY', 'STANDING_DUNK_TENDENCY', 'DRIVING_DUNK_TENDENCY', 'PUTBACK_TENDENCY', 'CRASH_TENDENCY', 'DRIVE_TENDENCY', 'POST_UP_TENDENCY',
               'SHOOT_FROM_POST_TENDENCY', 'SHOT_TENDENCY', 'TOUCHES_TENDENCY', 'ROLL_VS._POP_TENDENCY', 'PASS_INTERCEPTION_TENDENCY', 'TAKE_CHARGE_TENDENCY', 'ON-BALL_STEAL_TENDENCY', 'CONTEST_SHOT_TENDENCY', 'BLOCK_SHOT_TENDENCY', 'FOUL_TENDENCY']

    for i, c in enumerate(columns):
        tend_data[c] = tend_data_list[i]

    # Extract the "data" field from the JSON

    # Convert the dictionary to a DataFrame
    df = pd.DataFrame([tend_data], dtype=str)
    print(df)

    # Convert the DataFrame back to JSON format
    tend_json = df.to_json(orient='records')

    return tend_json

#@##################################

if len(module_data) != 10:
    print("Wrong Data Format!")
    raise(TypeError)

vit_json  = vital()
atr_json = attribute()
tend_json = tendency()
bad_json = badges()
output_file = "rating.txt"

print("COMPLETE!")

with open(output_file, "w", encoding="utf-8") as txt_file:
    txt_file.write("[" + "{\"module\":\"PLAYER\",\"tab\":\"VITALS\"," + "\"data\":" + atr_json.replace('[', '').replace(']', '').replace('\'', '\"').replace(" ", "") + "}" + 
                   "," + str(module_data["SHOES/GEAR"]).replace('\'', '\"').replace(" ", "") + 
                   "," + str(module_data["ACCESSORIES"]).replace('\'', '\"').replace(" ", "") + 
                   "," + "{\"module\":\"PLAYER\",\"tab\":\"ATTRIBUTES\"," + "\"data\":" + atr_json.replace('[', '').replace(']', '').replace('\'', '\"').replace(" ", "") + "}" + 
                   "," + "{\"module\":\"PLAYER\",\"tab\":\"TENDENCIES\"," + "\"data\":" + tend_json.replace('[', '').replace(']', '').replace('\'', '\"').replace(" ", "") + "}" + 
                   "," + str(module_data["HOTZONE"]).replace('\'', '\"').replace(" ", "") +
                   "," + str(module_data["SIGNATURE"]).replace('\'', '\"').replace(" ", "") +
                   "," + str(module_data["CONTRACT"]).replace('\'', '\"').replace(" ", "") +
                   "," + "{\"module\":\"PLAYER\",\"tab\":\"BADGES\"," + "\"data\":" + bad_json.replace('[', '').replace(']', '').replace('\'', '\"').replace(" ", "") + "}" + 
                   "," + str(module_data["STATS"]).replace('\'', '\"').replace(" ", "") +
                   "]")
