def american_odds_to_probability(odds):
    if odds > 0:
        return 100 / (odds + 100)
    else:
        return abs(odds) / (abs(odds) + 100)


def calculate_ev(probability, odds, bet_amount=100):
    if odds > 0:
        profit = odds / 100 * bet_amount
    else:
        profit = 100 / abs(odds) * bet_amount

    ev = (probability * profit) - ((1 - probability) * bet_amount)
    return ev


# Example odds for two teams (Team A and Team B)
team_a_yes_odds = 235  # Team A scores in 1st inning
team_a_no_odds = -340 # Team A doesn't score
team_b_yes_odds = 195 # Team B scores in 1st inning
team_b_no_odds = -275 # Team B doesn't score

# Convert odds to probabilities
team_a_yes_prob = american_odds_to_probability((team_a_yes_odds + abs(team_a_no_odds))/2)
team_b_yes_prob = american_odds_to_probability((team_b_yes_odds + abs(team_b_no_odds))/2)

# Probability any team scores in 1st inning
prob_any_team_scores = 1 - ((1 - team_a_yes_prob) * (1 - team_b_yes_prob))

# Calculate EV assuming fair odds (+100) for any team to score
fair_odds_any_team = 100
bet_amount = 100

ev_any_team_scores = calculate_ev(prob_any_team_scores, fair_odds_any_team, bet_amount)
ev_no_team_scores = calculate_ev(1 - prob_any_team_scores, fair_odds_any_team, bet_amount)

print(f"EV betting YES, any team scores: {ev_any_team_scores:.2f}")
print(f"EV betting NO, no team scores: {ev_no_team_scores:.2f}")