import pandas as pd
import json
import subprocess

data = [{"module": "PLAYER", "tab": "BADGES", "data": {"ALPHA_DOG": "0", "ENFORCER": "0", "RESERVED": "0", "FRIENDLY": "0", "TEAM_PLAYER": "0", "EXTREMELY_CONFIDENT": "0", "KEEP_IT_REAL": "0", "PAT_MY_BACK": "0", "EXPRESSIVE": "1", "UNPREDICTABLE": "0", "LAID_BACK": "0", "MEDIA_RINGMASTER": "0", "WARM_WEATHER_FAN": "0", "FINANCE_SAVVY": "0", "WORK_ETHIC": "0", "TRAILBLAZER": "0", "ULTIMATE_TRAILBLAZER": "0", "MOTIVATOR": "0", "ULTIMATE_MOTIVATOR": "0", "ACROBAT": "0", "AERIAL_WIZARD": "0", "BACKDOWN_PUNISHER": "0", "BULLY": "0", "DREAM_SHAKE": "0", "DROP-STEPPER": "0", "FAST_TWITCH": "0", "FEARLESS_FINISHER": "1", "GIANT_SLAYER": "0", "LIMITLESS_TAKEOFF": "0", "MASHER": "0", "POST_SPIN_TECHNICIAN": "0", "POSTERIZER": "0", "PRO_TOUCH": "0", "RISE_UP": "0", "SLITHERY": "0", "AGENT_3": "0", "AMPED": "0", "BLINDERS": "0", "CATCH_SHOOT": "0", "CLAYMORE": "0", "CLUTCH_SHOOTER": "0",
                                                       "COMEBACK_KID": "0", "CORNER_SPECIALIST": "1", "DEADEYE": "0", "GREEN_MACHINE": "1", "GUARD_UP": "0", "LIMITLESS_RANGE": "0", "MIDDY_MAGICIAN": "0", "SLIPPERY_OFF-BALL": "0", "SPACE_CREATOR": "0", "VOLUME_SHOOTER": "0", "ANKLE_BREAKER": "0", "BAIL_OUT": "1", "BREAK_STARTER": "0", "CLAMP_BREAKER": "0", "KILLER_COMBOS": "0", "DIMER": "0", "FLOOR_GENERAL": "0", "HANDLES_FOR_DAYS": "0", "HYPERDRIVE": "0", "MISMATCH_EXPERT": "0", "NEEDLE_THREADER": "0", "POST_PLAYMAKER": "0", "QUICK_FIRST_STEP": "0", "SPECIAL_DELIVERY": "0", "UNPLUCKABLE": "0", "VICE_GRIP": "0", "ANCHOR": "0", "ANKLE_BRACES": "0", "CHALLENGER": "0", "CHASE_DOWN_ARTIST": "0", "CLAMPS": "0", "GLOVE": "0", "INTERCEPTOR": "0", "MENACE": "0", "OFF-BALL_PEST": "0", "PICK_DODGER": "0", "POST_LOCKDOWN": "0", "POGO_STICK": "0", "WORK_HORSE": "0", "BRICK_WALL": "0", "BOXOUT_BEAST": "0", "REBOUND_CHASER": "0"}}]

new = input()
# Split the data into a list of values
new_list = new.split()

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

data_list = data[0]["data"]

for i, c in enumerate(columns):
    data_list[c] = new_list[i]
    # print(c, new_list[i])

# Extract the "data" field from the JSON

# Convert the dictionary to a DataFrame
df = pd.DataFrame([data_list], dtype=str)
print(df)


'''csv_file_path = 'rating.csv'

# Save the DataFrame to a CSV file
df.to_csv(csv_file_path, index=False)

#print(data[0]["data"])

# Open the CSV file in Excel
subprocess.Popen(['start', 'excel', csv_file_path], shell=True)'''

# Convert the DataFrame back to JSON format
json_data = df.to_json(orient='records')

# Define the output file path
output_file = "rating.txt"


with open(output_file, "w", encoding="utf-8") as txt_file:
    txt_file.write("[{\"module\":\"PLAYER\",\"tab\":\"BADGES\"," + "\"data\":" +
                   json_data.replace('[', '').replace(']', '').replace('\'', '\"').replace(" ", "")+"}]")
