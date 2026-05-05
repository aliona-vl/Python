class Category:
    def __init__(self, name):
        self.name = name
        self.ledger = []

    def deposit(self, amount, description=""):
        self.ledger.append({"amount": amount, "description": description})

    def withdraw(self, amount, description=""):
        if self.check_funds(amount):
            self.ledger.append({"amount": -amount, "description": description})
            return True
        return False

    def get_balance(self):
        return sum(item["amount"] for item in self.ledger)

    def transfer(self, amount, category):
        if self.check_funds(amount):
            self.withdraw(amount, f"Transfer to {category.name}")
            category.deposit(amount, f"Transfer from {self.name}")
            return True
        return False

    def check_funds(self, amount):
        return amount <= self.get_balance()

    def __str__(self):
        title = f"{self.name:*^30}\n"
        items = ""
        for item in self.ledger:
            description = item["description"][:23]
            amount = f"{item['amount']:.2f}"
            items += f"{description:<23}{amount:>7}\n"
        total = f"Total: {self.get_balance():.2f}"
        return title + items + total


def create_spend_chart(categories):
    # Calculate withdrawals for each category
    withdrawals = []
    for category in categories:
        total_withdrawals = sum(
            -item["amount"] for item in category.ledger if item["amount"] < 0
        )
        withdrawals.append(total_withdrawals)

    # Calculate percentages
    total_spent = sum(withdrawals)
    percentages = []
    for spent in withdrawals:
        if total_spent == 0:
            percentages.append(0)
        else:
            percent = int((spent / total_spent) * 100)
            percent = (percent // 10) * 10
            percentages.append(percent)

    # Build the chart
    chart = "Percentage spent by category\n"
    
    # Y-axis from 100 down to 0
    for i in range(100, -1, -10):
        chart += f"{i:>3}|"
        for percent in percentages:
            if percent >= i:
                chart += " o "
            else:
                chart += "   "
        chart += " \n"

    # Horizontal line
    chart += "    " + "-" * (len(categories) * 3 + 1) + "\n"

    # Category names vertically
    max_name_length = max(len(category.name) for category in categories)
    for i in range(max_name_length):
        chart += "     "
        for category in categories:
            if i < len(category.name):
                chart += f"{category.name[i]}  "
            else:
                chart += "   "
        if i < max_name_length - 1:
            chart += "\n"
    
    return chart