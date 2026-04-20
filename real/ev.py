import math

def calculate_probabilities(winning_odds):
    """
    Calculate the probability of winning and losing based on the winning odds.
    
    Args:
    winning_odds (float): The winning odds (can be positive or negative).
    
    Returns:
    tuple: (probability of winning, probability of losing)
    """
    if winning_odds < 0:  # Negative odds (e.g., -163)
        prob_win = abs(winning_odds) / (abs(winning_odds) + 100)
    else:  # Positive odds (e.g., +288)
        prob_win = 100 / (winning_odds + 100)
    
    prob_lose = 1 - prob_win
    return prob_win, prob_lose

def calculate_profit_win(betting_odds, bet_amount):
    """
    Calculate the profit if the pick wins based on the betting odds and bet amount.
    
    Args:
    betting_odds (float): The betting odds (can be positive or negative).
    bet_amount (float): The amount of the bet.
    
    Returns:
    float: The profit if the pick wins.
    """
    if betting_odds < 0:  # Negative odds (e.g., -142)
        profit_win = math.ceil(bet_amount * (1 / (abs(betting_odds) / 100)))
    else:  # Positive odds (e.g., +120)
        profit_win = bet_amount * (betting_odds / 100)
    
    return profit_win

def calculate_ev(winning_odds, betting_odds, bet_amount):
    """
    Calculate the expected value (EV) given winning odds, betting odds, and bet amount.
    
    Args:
    winning_odds (float): The winning odds (can be positive or negative).
    betting_odds (float): The betting odds (can be positive or negative).
    bet_amount (float): The amount of the bet.
    
    Returns:
    float: The calculated expected value (EV).
    """
    prob_win, prob_lose = calculate_probabilities(winning_odds)
    profit_win = calculate_profit_win(betting_odds, bet_amount)
    
    # Special condition: If bet amount is 10 and you lose, you lose 0
    if bet_amount == 10:
        loss_lose = 0
    else:
        loss_lose = bet_amount  # For any other amount, the loss is the bet amount
    
    ev = (profit_win * prob_win) - (loss_lose * prob_lose)
    return ev

def compare_ev(winning_odds1, betting_odds1, winning_odds2, betting_odds2):
    """
    Compare the expected value for four cases: betting 10 or 100 on team1/OVER or team2/UNDER.
    
    Args:
    winning_odds1 (float): The winning odds for team1 or OVER.
    betting_odds1 (float): The betting odds for team1 or OVER.
    winning_odds2 (float): The winning odds for team2 or UNDER.
    betting_odds2 (float): The betting odds for team2 or UNDER.
    """
    # Case 1: Betting 10 on team1/OVER
    ev1 = calculate_ev(winning_odds1, betting_odds1, 10)
    # Case 2: Betting 100 on team1/OVER
    ev2 = calculate_ev(winning_odds1, betting_odds1, 100)
    # Case 3: Betting 10 on team2/UNDER
    ev3 = calculate_ev(winning_odds2, betting_odds2, 10)
    # Case 4: Betting 100 on team2/UNDER
    ev4 = calculate_ev(winning_odds2, betting_odds2, 100)
    
    # Find the best EV
    ev_values = [ev1, ev2, ev3, ev4]
    best_ev = max(ev_values)
    best_case = ev_values.index(best_ev) + 1  # Cases are numbered 1-4
    
    # Print results with the best EV marked
    print(f"EV for betting 10 on team1/OVER: {ev1:.2f} {'<-- Best' if best_case == 1 else ''}")
    print(f"EV for betting 100 on team1/OVER: {ev2:.2f} {'<-- Best' if best_case == 2 else ''}")
    print(f"EV for betting 10 on team2/UNDER: {ev3:.2f} {'<-- Best' if best_case == 3 else ''}")
    print(f"EV for betting 100 on team2/UNDER: {ev4:.2f} {'<-- Best' if best_case == 4 else ''}")

# Example usage
winning_odds1 = 108 # Replace with the winning odds for team1 or OVER
betting_odds1 = 104 # Replace with the betting odds for team1 or OVER
winning_odds2 = -108 # Replace with the winning odds for team2 or UNDER
betting_odds2 = -112   # Replace with the betting odds for team2 or UNDER

compare_ev(winning_odds1, betting_odds1, winning_odds2, betting_odds2)
