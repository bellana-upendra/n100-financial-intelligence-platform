"""
Sprint 4 - Investment Recommendation Engine

Combines:
Ranking score + Risk category
"""

from src.analytics.sprint4.ranking import generate_ranking
from src.analytics.sprint4.risk import calculate_risk


def generate_recommendations():

    ranking = generate_ranking()

    risk = calculate_risk()

    df = ranking.merge(risk, on="company_id", how="left")

    print(df.columns.tolist())

    # Rename actual risk score
    if "risk_score_y" in df.columns:
        df.rename(columns={"risk_score_y": "risk_score"}, inplace=True)

    def recommendation(row):

        score = row["investment_score"]
        risk_category = row["risk_category"]

        if score >= 75 and risk_category == "Low Risk":
            return "Strong Buy"

        elif score >= 60:
            return "Buy"

        elif score >= 45:
            return "Hold"

        else:
            return "Avoid"

    df["recommendation"] = df.apply(recommendation, axis=1)

    return df[
        [
            "company_id",
            "investment_score",
            "risk_score",
            "risk_category",
            "recommendation",
            "rank",
        ]
    ].sort_values("rank")


if __name__ == "__main__":

    result = generate_recommendations()

    print("Recommendation Engine Completed")
    print("==============================")

    print(result.head(20).to_string(index=False))
