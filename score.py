import pandas as pd
import numpy as np
import math

###### CHANGE THIS PATH TO WHEREVER YOU HAVE THE CSV FILE DOWNLOADED ######
file_path = "grants-search-202608182008.csv"

def average_from_award_range(x):
    # -\_ graph
    return int((-10 / (1 + math.exp(-x + 4))) + 5) #if x >= 0 else int(x) - 1

def award_range(ceiling, floor):
    # scores the award_range category for each grant as the average of the award_ceiling and award_floor columns
    # input: two numbers from the dataframe, award_ceiling and award_floor
    # output: the average of the two columns
    return (ceiling + floor) / 2


def expected_number_of_awards(x):
    # _/- graph
    # for the category expected_number_of_awards
    # x = number of awards for that grant
    if x <= 0:
        return 0
    else:
        return int((30 / (1 + math.exp(-0.1 * x))) - 15)


df = pd.read_csv(file_path)
df['award_range_avg'] = award_range(df['award_ceiling'], df['award_floor'])
df['award_range_score'] = df['award_range_avg'].apply(average_from_award_range)
df['expected_awards_score'] = df['expected_number_of_awards'].apply(expected_number_of_awards)

print(df.columns.tolist())
#ceiling = df['award_ceiling']
#floor = df['award_floor']
#print("Average from award range:", average_from_award_range(3))
