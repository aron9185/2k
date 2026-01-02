import pandas as pd

# Load the two CSV files
csv_file1 = "splitsshooting26.csv"  # Replace with your first CSV file name
csv_file2 = "splitsshooting_old.csv"  # Replace with your second CSV file name

# Read the CSV files into pandas DataFrames
df1 = pd.read_csv(csv_file1)
df2 = pd.read_csv(csv_file2)

# Concatenate the DataFrames, aligning them by column names
result = pd.concat([df1, df2], ignore_index=True)

# Save the concatenated DataFrame to a new CSV file
output_file = "concatenated_output.csv"
result.to_csv(output_file, index=False)

print(f"Concatenated file saved as {output_file}")
