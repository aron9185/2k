import pandas as pd

# Read data from the CSV file back into a DataFrame
df_read = pd.read_csv('rating.csv', dtype=str)

# Convert the DataFrame back to JSON format
json_data = df_read.to_json(orient='records')

# Define the output file path
output_file = "rating.txt"

with open(output_file, "w", encoding="utf-8") as txt_file:
    txt_file.write("[{\"module\":\"PLAYER\",\"tab\":\"ATTRIBUTES\"," + "\"data\":" + json_data.replace('[', '').replace(']', '').replace('\'', '\"').replace(" ", "")+"}]")