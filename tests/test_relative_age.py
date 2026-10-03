"""Checks on the age text that lands in injected content.

The agent reading injected text is poor at date arithmetic, which is why the
arithmetic is done before it gets there. That makes this helper's boundaries the
whole feature. Three bugs showed up while writing it, and each has a test here:
one year read as "0y", two years read as "1y", and "now" grew a suffix to become
"now ago".
"""

from datetime import UTC, datetime, timedelta

import pytest

from mgcp.models import age_phrase, relative_age

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def ago(**kwargs):
    return NOW - timedelta(**kwargs)


class TestRelativeAge:
    @pytest.mark.parametrize(
        "delta,expected",
        [
            ({"minutes": 1}, "now"),
            ({"minutes": 59}, "now"),
            ({"hours": 1}, "1h"),
            ({"hours": 23}, "23h"),
            ({"days": 1}, "1d"),
            ({"days": 29}, "29d"),
            ({"days": 30}, "1mo"),
            ({"days": 45}, "1mo"),
            ({"days": 46}, "2mo"),
            ({"days": 210}, "7mo"),
            ({"days": 364}, "11mo"),
            ({"days": 365}, "1y"),
            ({"days": 548}, "2y"),
            ({"days": 730}, "2y"),
            ({"days": 1095}, "3y"),
        ],
    )
    def test_the_ladder(self, delta, expected):
        assert relative_age(ago(**delta), NOW) == expected

    def test_a_year_is_never_zero(self):
        """Truncation made 365 days read as 0y."""
        assert relative_age(ago(days=365), NOW) == "1y"

    def test_two_years_is_not_one(self):
        """Truncation made 730 days read as 1y."""
        assert relative_age(ago(days=730), NOW) == "2y"

    def test_the_month_bucket_never_claims_a_year(self):
        """Rounding 364 days gives 12 months, which would contradict the next bucket."""
        for days in range(330, 365):
            value = relative_age(ago(days=days), NOW)
            assert value.endswith("mo"), (days, value)
            assert int(value[:-2]) <= 11, (days, value)

    def test_every_step_is_short(self):
        """The point is to cost almost nothing, so no value runs long."""
        for days in (0, 1, 15, 29, 30, 100, 364, 365, 900, 4000):
            assert len(relative_age(ago(days=days), NOW)) <= 7

    def test_a_naive_datetime_is_read_as_utc(self):
        """Imported notes and old rows carry no offset, and a crash here would
        take out the whole injected block."""
        assert relative_age(datetime(2026, 10, 1, 12, 0), NOW) == "1d"

    def test_missing_is_not_a_crash(self):
        assert relative_age(None, NOW) == "unknown"

    def test_a_future_timestamp_does_not_produce_a_negative(self):
        """A clock change or a bad import must not render "-3d"."""
        assert relative_age(NOW + timedelta(days=3), NOW) == "now"


class TestAgePhrase:
    def test_it_adds_the_suffix(self):
        assert age_phrase(ago(days=5), "ago", NOW) == "5d ago"
        assert age_phrase(ago(days=5), "old", NOW) == "5d old"

    def test_now_does_not_take_a_suffix(self):
        """This produced "now ago" in the project header and "now old" on a
        lesson refined minutes earlier."""
        assert age_phrase(ago(minutes=5), "ago", NOW) == "now"
        assert age_phrase(ago(minutes=5), "old", NOW) == "now"

    def test_unknown_does_not_take_a_suffix(self):
        assert age_phrase(None, "ago", NOW) == "unknown"


class TestItReachesTheInjectedText:
    def test_the_project_header_and_todos_carry_an_age(self):
        from mgcp.models import ProjectContext, ProjectTodo

        context = ProjectContext(
            project_id="p",
            project_name="P",
            project_path="/tmp/p",
            last_accessed=ago(days=3),
            todos=[ProjectTodo(content="an old todo", created_at=ago(days=210))],
        )
        rendered = context.to_context()
        assert "3d ago" in rendered, rendered[:200]
        assert "(7mo) an old todo" in rendered, rendered
