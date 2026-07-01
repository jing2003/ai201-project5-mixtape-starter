"""
tests/test_feed.py - Mixtape

Regression tests for the Friends Listening Now feed.
"""

import pytest
from datetime import datetime, timedelta, timezone
from app import create_app, db
from models import User, Song, ListeningEvent, friendships
from services.feed_service import get_activity_feed, get_friends_listening_now


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def feed_data(app):
    """Create one current user, one recent friend, and one stale friend."""
    with app.app_context():
        current_user = User(username="listener", email="listener@example.com")
        recent_friend = User(username="recent_friend", email="recent@example.com")
        stale_friend = User(username="stale_friend", email="stale@example.com")
        db.session.add_all([current_user, recent_friend, stale_friend])
        db.session.flush()

        db.session.execute(
            friendships.insert().values(user_id=current_user.id, friend_id=recent_friend.id)
        )
        db.session.execute(
            friendships.insert().values(user_id=recent_friend.id, friend_id=current_user.id)
        )
        db.session.execute(
            friendships.insert().values(user_id=current_user.id, friend_id=stale_friend.id)
        )
        db.session.execute(
            friendships.insert().values(user_id=stale_friend.id, friend_id=current_user.id)
        )

        recent_song = Song(
            title="Still Playing",
            artist="Now Artist",
            shared_by=current_user.id,
        )
        stale_song = Song(
            title="Yesterday Energy",
            artist="Old Artist",
            shared_by=current_user.id,
        )
        db.session.add_all([recent_song, stale_song])
        db.session.flush()

        now = datetime.now(timezone.utc)
        db.session.add_all([
            ListeningEvent(
                user_id=recent_friend.id,
                song_id=recent_song.id,
                listened_at=now - timedelta(minutes=10),
            ),
            ListeningEvent(
                user_id=stale_friend.id,
                song_id=stale_song.id,
                listened_at=now - timedelta(minutes=45),
            ),
        ])
        db.session.commit()

        yield {
            "current_user_id": current_user.id,
            "recent_friend_id": recent_friend.id,
            "stale_friend_id": stale_friend.id,
        }


def test_listening_now_excludes_friend_outside_recent_window(app, feed_data):
    """
    Friends Listening Now should only include very recent listening activity.

    The stale friend listened 45 minutes ago. This would have failed when the
    recent threshold was 24 hours because that friend still passed the cutoff.
    """
    with app.app_context():
        feed = get_friends_listening_now(feed_data["current_user_id"])
        friend_ids = {item["friend"]["id"] for item in feed}

        assert feed_data["recent_friend_id"] in friend_ids
        assert feed_data["stale_friend_id"] not in friend_ids


def test_activity_feed_still_includes_older_friend_activity(app, feed_data):
    """The general activity feed is separate from the Listening Now window."""
    with app.app_context():
        feed = get_activity_feed(feed_data["current_user_id"])
        friend_ids = {item["friend"]["id"] for item in feed}

        assert feed_data["recent_friend_id"] in friend_ids
        assert feed_data["stale_friend_id"] in friend_ids
