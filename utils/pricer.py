def estimate_future_premiums(current_premium, delta, gamma, theta, current_spot_price, proximal, distal, days_to_expiry):
    """
    Estimate future option premiums for CE and PE when the spot price reaches the proximal and distal lines.
    
    :param current_premium: Current premium of the option
    :param delta: Current Delta of the option
    :param gamma: Current Gamma of the option
    :param theta: Current Theta of the option (per day decay)
    :param current_spot_price: Current spot price of the underlying asset
    :param proximal: Proximal line value for the trade
    :param distal: Distal line value for the trade
    :param days_to_expiry: Number of days to the option's expiry
    :return: Estimated premiums at proximal and distal lines
    """

    def calculate_premium(spot_price_change, option_type):
        adjusted_delta = -delta if option_type == 'PE' else delta
        delta_change = adjusted_delta * spot_price_change
        gamma_change = 0.5 * gamma * spot_price_change ** 2
        theta_change = theta * days_to_expiry
        return current_premium + delta_change + gamma_change + theta_change

    # Determine option type and calculate price changes
    if current_spot_price > proximal and current_spot_price > distal:
        # Potential bullish reversal for CE
        option_type = 'CE'
        proximal_change = proximal - current_spot_price
        distal_change = distal - current_spot_price
    else:
        # Potential bearish reversal for PE
        option_type = 'PE'
        proximal_change = current_spot_price - proximal
        distal_change = current_spot_price - distal

    # Calculate premiums at proximal and distal points
    premium_at_proximal = calculate_premium(proximal_change, option_type)
    premium_at_distal = calculate_premium(distal_change, option_type)

    return premium_at_proximal, premium_at_distal

# Example usage
current_premium = 5.0  # Current option premium
delta = 0.6  # Delta of the option
gamma = 0.1  # Gamma of the option
theta = -0.05  # Theta of the option (per day decay)
current_spot_price = 120  # Current spot price of the underlying asset (higher than both proximal and distal)
proximal = 110  # Proximal line value
distal = 105  # Distal line value (stop loss)
days_to_expiry = 30  # Days to expiry

premium_at_proximal, premium_at_distal = estimate_future_premiums(current_premium, delta, gamma, theta, current_spot_price, proximal, distal, days_to_expiry)
print(f"Premium at Proximal Line: {premium_at_proximal}")
print(f"Premium at Distal Line: {premium_at_distal}")
