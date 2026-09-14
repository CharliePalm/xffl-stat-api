from shared.calculation_utils import calculate_defense_score, calculate_offensive_score
from shared.service.engine import SessionLocal
from shared.service.player_week_service import PlayerWeekService
from shared.service.service import Page
from shared.service.player_statline_service import PlayerStatlineService
from shared.model import NFLPosition


def main():
    session = SessionLocal()
    player_week_service = PlayerWeekService(session)
    statline_service = PlayerStatlineService(session)
    with session.begin():
        offset = 0
        while True:
            results = statline_service.search(page=Page(limit=200, offset=offset))
            for line in results.items:
                if line.position == NFLPosition.D:
                    points = calculate_defense_score(line)
                else:
                    points = calculate_offensive_score(line)
                player_week_service.update(
                    (line.player_id, line.week), {"points": points}
                )
            offset += len(results.items)
            if not results.has_more:
                break


if __name__ == "__main__":
    main()
