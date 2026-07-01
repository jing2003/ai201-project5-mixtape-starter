# Mixtape

Mixtape is a small Flask + SQLAlchemy social music app. The main pattern is: Flask routes receive HTTP requests, routes validate and format inputs/outputs, service functions handle the application logic, and SQLAlchemy models define the database structure.

## AI Usage

I used AI tools during this project mainly for codebase navigation, debugging support, and writing clearer root cause analysis notes. I did not use AI as a replacement for testing the bugs myself. I used the AI explanations as guidance, then verified the behavior by reading the actual files, running tests, using `flask shell`, and checking the output.

One way I used AI was during codebase orientation. I asked AI to help explain the roles of the main files, including `app.py`, `models.py`, the route files, and the service files. This helped me understand the project structure: routes handle request parsing and response formatting, while the service layer contains most of the business logic. I used that explanation to write my codebase map, but I verified it by opening the actual files and tracing routes to their service functions.

I also used AI while investigating Issue #1, the listening streak bug. I gave AI the `update_listening_streak()` function and asked it to explain what the logic was doing. AI helped me notice that `today.weekday() != 6` excluded Sundays from the consecutive-day increment case. I verified this myself by running `pytest tests/test_streaks.py -q` and seeing the Sunday streak test fail before the fix. I then removed the Sunday exclusion and reran the tests to confirm the behavior was fixed.

For Issue 2, I used AI to help polish my reasoning about `RECENT_THRESHOLD` in `services/feed_service.py`. The AI helped me phrase why `timedelta(hours=24)` was too broad for “Friends Listening Now” and suggested checking both sides of the boundary. I verified this by looking at `seed_data.py`, which says recent listening events from the past 30 minutes should appear, while older events should not. I also added a regression test with one friend who listened 10 minutes ago and another who listened 45 minutes ago.

For Issue #3, I asked AI to explain the `outerjoin(song_tags, Song.id == song_tags.c.song_id)` line in the search query. AI helped me understand that joining a song to the `song_tags` table can produce multiple joined rows for one multi-tag song. I verified this in `flask shell` by querying `Song.title` and `song_tags.c.tag_id` directly and seeing `"Crown Heights Anthem"` appear three times at the joined-row level. I also checked the actual service behavior and learned that SQLAlchemy may collapse duplicate ORM objects, so I did not rely only on AI’s explanation. I verified the issue through the database query and then removed the unnecessary join from the search function.

For Issue #4, I used AI to explain the `rate_song()` function step by step. This helped me see that the function validated the score, found the song and user, created or updated the rating, and committed the change, but never created a notification for the original song sharer. I verified this in `flask shell` by counting the sharer’s notifications before and after rating a song. The rating was saved, but the notification count did not change. I then added a notification call before the commit and checked that the sharer received a notification when someone else rated their song.

For Issue 5, I asked what this line did:

```python
return [song.to_dict() for song in songs[:-1]]
```

The AI explained that `songs[:-1]` returns every item except the last one. I verified that explanation by comparing it to the failing playlist test: the test created 5 songs but only 4 were returned. That made the slice explanation match the actual bug.

In a few places, I had to verify or refine the AI output. For example, the AI explanation for the search duplicate issue made sense conceptually, but my first service-level test passed because SQLAlchemy returned one ORM object per primary key. I then checked the raw joined rows myself in `flask shell` to confirm what was actually happening. This helped me avoid documenting something I had not reproduced through the code.

## Codebase Map

### Main files

- `app.py` creates the Flask app, configures SQLAlchemy, registers the route blueprints, and creates database tables inside the app context. The app exposes four URL groups: `/songs`, `/playlists`, `/users`, and `/feed`.
- `models.py` defines the database models and join tables:
  - `User` stores account/profile data, listening streak fields, notifications, playlists, ratings, listening events, shared songs, and a self-referential `friends` relationship.
  - `Song` stores shared song metadata, the user who shared it, optional share notes, ratings, listening events, and tags.
  - `Tag` stores reusable tag names. Songs and tags connect through the `song_tags` join table.
  - `ListeningEvent` records that a user listened to a song at a specific time.
  - `Rating` stores one score per user/song pair using a uniqueness constraint on `(user_id, song_id)`.
  - `Playlist` stores playlist metadata. Songs connect through the `playlist_entries` join table, which includes `position`, `added_by`, and `added_at`, so playlist membership has ordering and attribution.
  - `Notification` stores messages for users, with a type, body, timestamp, and read/unread state.
- `routes/songs.py` handles song search, song detail, rating a song, and recording a listen. It delegates search/detail to `search_service`, rating to `notification_service.rate_song()`, and listening/streak updates to `streak_service.record_listening_event()`.
- `routes/playlists.py` handles playlist creation, playlist metadata, playlist songs, and adding a song to a playlist. Creation/retrieval lives in `playlist_service`; adding a song goes through `notification_service.add_to_playlist()` because that action may notify the original song sharer.
- `routes/users.py` handles user profile lookup, streak lookup, notification listing, and marking notifications as read. Unlike most routes, the basic user profile endpoint reads `User` directly through `db.session.get()` instead of going through a service.
- `routes/feed.py` handles the social feed endpoints: friends listening now and friend activity. Both delegate to `feed_service`.
- `services/streak_service.py` contains listening streak logic. `record_listening_event()` creates a `ListeningEvent`, calls `update_listening_streak()`, and commits both changes together. `update_listening_streak()` decides whether to start, increment, keep, or reset the streak based on the user's previous listening date.
- `services/feed_service.py` builds feed responses from friends' `ListeningEvent` records. `get_friends_listening_now()` filters recent events, deduplicates to the latest event per friend, and returns friend/song/listened_at dictionaries. `get_activity_feed()` returns the latest friend listening events up to a limit.
- `services/search_service.py` searches songs by title or artist and returns serialized song dictionaries with tag names included by `Song.to_dict()`. It also has `get_song()` for single-song lookup.
- `services/notification_service.py` creates and reads notifications, marks them read, saves ratings, and handles the "add song to playlist" flow. This file is where user interactions with someone else's shared song can become `Notification` rows.
- `services/playlist_service.py` creates playlists, reads playlist metadata, reads a user's playlists, and queries ordered playlist songs through the `playlist_entries` table.
- `seed_data.py` resets and populates the development database with users, friendships, tags, songs, listening events, playlists, and sample notifications for local testing.
- `tests/` contains focused service-level tests. `test_streaks.py` checks listening streak behavior, `test_search.py` checks song search behavior, `test_playlists.py` checks playlist retrieval and ordering, and `test_feed.py` checks feed recency behavior.
- `README.md` explains setup, the intended architecture, and example call chains from routes into services.
- `requirements.txt` lists the Python dependencies: Flask, Flask-SQLAlchemy, pytest, and related packages.

### Data flow: adding a song to a playlist triggers a notification

1. A client sends `POST /playlists/<playlist_id>/songs` with JSON containing `song_id` and `added_by`.
2. `routes/playlists.py` parses the request body and checks that both fields are present.
3. The route calls `notification_service.add_to_playlist(playlist_id, song_id, added_by)`.
4. `add_to_playlist()` loads the `Song`, the adding `User`, and the `Playlist` from the database. If any are missing, it raises `ValueError`, which the route turns into a `400` JSON error response.
5. If the song is not already on the playlist, the service appends it to `playlist.songs` and commits the playlist change.
6. If the person adding the song is not the same user who originally shared the song, `add_to_playlist()` calls `create_notification()`.
7. `create_notification()` inserts a `Notification` for `song.shared_by` with type `song_added_to_playlist` and a body naming the adder, song title, and playlist name.
8. The route returns `201` with `{"message": "Song added to playlist"}`. Later, the original sharer can fetch that message through `GET /users/<user_id>/notifications`, which calls `notification_service.get_notifications()`.

Important detail: playlist ordering is modeled in the `playlist_entries` table, not on `Song` itself. That table also has required `position` and `added_by` columns, so playlist writes need to preserve association-table metadata.

### Data flow: listening to a song updates a streak

1. A client sends `POST /songs/<song_id>/listen` with JSON containing `user_id`.
2. `routes/songs.py` validates that `user_id` is present and calls `streak_service.record_listening_event(user_id, song_id)`.
3. `record_listening_event()` loads the `User`, creates a new `ListeningEvent` with the current UTC timestamp, and calls `update_listening_streak(user, now)`.
4. `update_listening_streak()` compares today's date to `user.last_listened_at`:
   - no previous listen starts the streak at 1;
   - another listen on the same day leaves the streak unchanged;
   - a consecutive-day listen should increment the streak;
   - a skipped day resets the streak to 1.
5. The service commits the new listening event plus the updated user streak in the same database transaction.
6. The route returns the new listening event as JSON with status `201`. The current streak can then be read through `GET /users/<user_id>/streak`.

### Patterns I noticed

- The app uses an application factory (`create_app`) instead of a single global Flask app. This makes the tests easy to run with an in-memory SQLite database.
- Most route functions are intentionally thin. They read path/query/body data, validate required fields, call a service, and translate `ValueError` into JSON error responses.
- Business logic mostly lives in `services/`, keeping the Flask route handlers small and making service functions the main place to read feature behavior.
- Models expose `to_dict()` methods, so services and routes usually return serialized model data without duplicating formatting code.
- UUID strings are used as primary keys across the app.
- Many-to-many relationships use explicit association tables: `friendships` for user-to-user friendship, `song_tags` for tags, and `playlist_entries` for playlist songs with extra metadata.
- The tests target service functions directly rather than going through HTTP routes, so behavior checks stay close to the business logic.
- A few files contain unused imports or uneven delegation. For example, `routes/playlists.py` imports `get_user_playlists` but does not expose a route for it, and `notification_service.add_to_playlist()` imports `get_playlist_songs` but does not call it.
- Notifications are not a separate background system. They are created synchronously during service calls and stored as normal database rows.

## Root Cause Analysis

### Issue 1: My listening streak keeps resetting

#### How I reproduced it

I reproduced this by using the existing Sunday boundary case in `tests/test_streaks.py`, specifically `test_streak_increments_on_sunday`. The test creates a user, then calls `update_listening_streak()` twice with controlled timestamps:

- Saturday, June 15, 2024 at 12:00 UTC
- Sunday, June 16, 2024 at 12:00 UTC

Those dates are consecutive calendar days, so the expected streak after the Sunday listen is `2`. Before the fix, the streak became `1` instead. That confirmed the data condition that triggered the bug: a user who listened yesterday would still have their streak reset if today's listen happened on Sunday.

#### How I found the root cause

I started from the symptom: listening streaks only change when the app records that a user listened to a song. From there, I traced the route that handles that action. In `routes/songs.py`, the endpoint `POST /songs/<song_id>/listen` is handled by the `listen()` function. That route reads `user_id` from the request body and calls `record_listening_event(user_id, song_id)`.

At the top of `routes/songs.py`, `record_listening_event` is imported from `services.streak_service`, so I moved into `services/streak_service.py`. In `record_listening_event()`, the service creates a `ListeningEvent`, then calls `update_listening_streak(user, now)`. That made `update_listening_streak()` the exact function to inspect because it is where `user.listening_streak` and `user.last_listened_at` are changed.

The moment I was confident I had found the specific cause was when I saw this condition:

`elif days_since_last == 1 and today.weekday() != 6:`

The failing reproduction case used Sunday as the second listening day, and Python's `date.weekday()` returns `6` for Sunday. That connected the failing input directly to the conditional that skipped the increment path.

#### The root cause

The root cause was an unnecessary Sunday exclusion in `update_listening_streak()`. The function correctly calculates whether the current listen happened one calendar day after the previous listen:

`days_since_last = (today - last_date).days`

For Saturday, June 15 to Sunday, June 16, `days_since_last` is `1`, which should increment the streak. However, the code also required `today.weekday() != 6`. Since Python represents Sunday as `6`, the condition was false on Sundays even when the user really had listened on consecutive days.

Because that `elif` did not run, the function fell into the `else` branch and reset `user.listening_streak` to `1`. The date subtraction was not the problem; the problem was treating Sunday as an invalid day for a consecutive streak, even though the app's own streak rules say any yesterday-to-today listen should increment.

#### My fix and side-effect check

I made the fix in `services/streak_service.py` by removing the Sunday-specific condition. The increment rule changed from:

`elif days_since_last == 1 and today.weekday() != 6:`

To:

`elif days_since_last == 1:`

This is a targeted fix because it changes only the condition that incorrectly rejected Sunday. It keeps the rest of the streak behavior the same: first listens still start a streak, same-day listens still do not double-count, and skipped days still reset the streak.

For the side-effect check, I verified the related boundary cases covered by `tests/test_streaks.py`:

- A new user's first listen starts the streak at `1`.
- Listening on two non-Sunday consecutive days increments the streak.
- Listening twice on the same day does not increment twice.
- Skipping a day resets the streak to `1`.
- Listening on Saturday and then Sunday now increments the streak to `2` instead of resetting.

Those checks matter because the fix touched the central streak branch. Passing both sides of the boundary showed that removing the Sunday exception fixed the reported bug without changing the intended behavior for same-day listens, skipped days, or normal consecutive days.

### Issue #2: Friends Listening Now shows people from yesterday

#### How I reproduced it

I reproduced the bug by setting up a user with a friend who had a `ListeningEvent` that was older than the intended "now" window but still within the last 24 hours, such as 23 hours ago. Then I called the Friends Listening Now feature for the current user.

The expected behavior was that this friend should not appear, because "Friends Listening Now" should only show very recent listening activity. The actual behavior was that the friend still appeared in the listening-now feed. This confirmed that the bug was triggered when a friend had a listening event within the last 24 hours, even if the event was not actually recent enough to count as "now."

I also checked `seed_data.py`, which indicated that listening events from the past 30 minutes should appear in Friends Listening Now, while older events should not.

#### How I found the root cause

I started from the feature name, "Friends Listening Now," which pointed me to the feed-related code. I opened `routes/feed.py` and found the listening-now route:

```python
@feed_bp.route("/<user_id>/listening-now")
def listening_now(user_id):
```

That route calls:

```python
get_friends_listening_now(user_id)
```

from `services/feed_service.py`.

In `services/feed_service.py`, I looked at `get_friends_listening_now()`. The function calculates a cutoff time before querying listening events:

```python
RECENT_THRESHOLD = timedelta(hours=24)
cutoff = datetime.now(timezone.utc) - RECENT_THRESHOLD
```

That made me confident I had found the root cause because the code was defining "recent" as the last 24 hours. A 24-hour window can include songs from yesterday, which matches the reported bug exactly.

#### The root cause

The root cause was that `RECENT_THRESHOLD` was set to 24 hours:

```python
RECENT_THRESHOLD = timedelta(hours=24)
```

The query includes any friend listening event where:

```python
ListeningEvent.listened_at >= cutoff
```

Since the cutoff was 24 hours ago, the feed treated anything from the last full day as "listening now." That is too broad for a feature called Friends Listening Now. A friend who listened yesterday, or many hours ago, could still pass the cutoff check and appear in the feed.

The query logic was working as written, but the definition of "recent" was wrong for this feature.

#### My fix and side-effect check

I fixed the bug by changing the threshold from 24 hours to 30 minutes:

```python
RECENT_THRESHOLD = timedelta(minutes=30)
```

This keeps the existing query structure the same, but changes the time window so only truly recent listening events appear.

For the side-effect check, I verified both sides of the boundary:

- A friend who listened within the last 30 minutes still appears in Friends Listening Now.
- A friend who listened more than 30 minutes ago no longer appears.
- A user with no friends still gets an empty feed.
- The general activity feed still works separately because `get_activity_feed()` does not use `RECENT_THRESHOLD`; it is supposed to show older friend activity.

That check matters because the fix should only narrow the "listening now" window, not break friend lookup, feed formatting, or the separate activity feed.

### Issue 3: The same song keeps showing up twice in search

#### How I reproduced it

I reproduced the duplicate search issue with the seeded multi-tag song `"Crown Heights Anthem"`. This song has multiple tags, so it is a good test case for checking whether the search query returns one song result or one result per tag.

I first searched for `"Crown Heights"` through the search behavior and expected the song to appear once. The buggy behavior was that the same song could appear multiple times.

To confirm why that was happening, I also inspected the database rows directly in `flask shell` with a joined query between `Song` and the `song_tags` table:

```python
rows = (
    db.session.query(Song.title, song_tags.c.tag_id)
    .outerjoin(song_tags, Song.id == song_tags.c.song_id)
    .filter(Song.title.ilike("%Crown Heights%"))
    .all()
)
```

That returned multiple rows for the same song title because `"Crown Heights Anthem"` has multiple tag rows. This confirmed the data condition that triggered the bug: a song with more than one tag could be expanded into multiple joined rows during search.

#### How I found the root cause

I started in `routes/songs.py` because the bug appeared in song search. The `/songs/search` route reads the `q` query parameter and passes it into:

```python
search_songs(query)
```

That function comes from `services/search_service.py`, so I opened that file next.

In `search_songs()`, I found this query:

```python
results = (
    db.session.query(Song)
    .outerjoin(song_tags, Song.id == song_tags.c.song_id)
    .filter(
        db.or_(
            Song.title.ilike(f"%{query}%"),
            Song.artist.ilike(f"%{query}%"),
        )
    )
    .all()
)
```

The suspicious part was:

```python
.outerjoin(song_tags, Song.id == song_tags.c.song_id)
```

That made me confident I had found the root cause because the search filter only checks `Song.title` and `Song.artist`. It does not search tag names, so joining to `song_tags` was unnecessary. Since `song_tags` can contain multiple rows for one song, the join was exactly the kind of operation that could turn one matching song into multiple result rows.

#### The root cause

The root cause was an unnecessary join from `Song` to the many-to-many `song_tags` table inside `search_songs()`.

The search feature only needs to match against:

```python
Song.title
Song.artist
```

But the query also joined against `song_tags`. Because `song_tags` stores one row per song/tag pair, a song with three tags has three matching joined rows. That means a single song could be expanded by the database into multiple rows before the results were returned.

The title and artist filters were not the problem. The specific problem was joining to a table where one song can appear multiple times, even though the joined table was not needed for filtering.

#### My fix and side-effect check

I fixed the bug by removing the unnecessary `outerjoin()` from `search_songs()`. The query now searches the `Song` table directly:

```python
results = (
    db.session.query(Song)
    .filter(
        db.or_(
            Song.title.ilike(f"%{query}%"),
            Song.artist.ilike(f"%{query}%"),
        )
    )
    .all()
)
```

This fixes the root cause because the query no longer expands one song into multiple joined rows.

For the side-effect check, I verified the related search behaviors:

- Searching by song title still returns matching songs.
- Searching by artist still returns matching songs.
- A song with no tags can still appear in search results.
- A song with one tag appears once.
- A song with multiple tags, like `"Crown Heights Anthem"`, still appears, but only once.
- A query with no matches still returns an empty list.

I also checked that removing the join did not remove tags from the response. Tags are still included through `Song.to_dict()`, which reads the song’s `tags` relationship separately. So the fix only removes the duplicate-producing join; it does not remove tag data from the search response.

### Issue 4: I got notified when a friend added my song to a playlist but not when they rated it

#### How I reproduced it

I reproduced the bug in `flask shell` by selecting a seeded song and then choosing a different user to rate it. I made sure the rater was not the same user who originally shared the song.

Before calling `rate_song()`, I counted the notifications for the original song sharer. Then I called:

```python
rate_song(rater.id, song.id, 5)
```

The rating was saved successfully, but when I counted the sharer’s notifications again, the count had not increased. That confirmed the bug: rating someone else’s song created a `Rating` record, but it did not create a notification for the original sharer.

#### How I found the root cause

I started in `routes/songs.py` because rating a song is a song action. The route for rating is:

```python
@songs_bp.route("/<song_id>/rate", methods=["POST"])
def rate(song_id):
```

That route reads `user_id` and `score` from the request body, then calls:

```python
rating = rate_song(user_id, song_id, int(score))
```

At the top of the file, `rate_song` is imported from `services.notification_service`, so I opened `services/notification_service.py`.

In that file, I compared two related flows:

- `add_to_playlist()` adds a song to a playlist and calls `create_notification()` for the original song sharer.
- `rate_song()` creates or updates a `Rating`, commits it, and returns the rating.

The important moment was noticing that `rate_song()` never calls `create_notification()`. The helper already existed in the same file, and the playlist flow showed the intended pattern for notifying a song’s original sharer. The rating flow simply skipped that step.

#### The root cause

The root cause was a missing notification step in `rate_song()`.

The function correctly validated the score, loaded the `Song`, loaded the rater, and created or updated the `Rating`. But after saving the rating, it returned immediately without notifying `song.shared_by`.

So the rating data was persisted, but the side effect users expected did not happen. The code path for adding a song to a playlist had notification logic, but the code path for rating a song did not.

#### My fix and side-effect check

I fixed the bug by adding a `create_notification()` call inside `rate_song()` after the rating is saved. The notification should only be created when the rater is not the original sharer:

```python
if song.shared_by != user_id:
    create_notification(
        user_id=song.shared_by,
        notification_type="song_rated",
        body=f"{rater.username} rated your song '{song.title}' {score}/5.",
    )
```

This fixes the root cause because the rating flow now performs the missing side effect: it creates a notification for the person who originally shared the song.

For the side-effect check, I verified:

- Rating someone else’s song still saves the `Rating`.
- Rating someone else’s song now creates a `song_rated` notification for the original sharer.
- Rating your own song does not notify yourself.
- Updating an existing rating still updates the saved score.
- Existing playlist-add notifications still work because `add_to_playlist()` uses the same `create_notification()` helper separately.

That check matters because the fix adds a side effect to an existing write path. I wanted to make sure ratings still save correctly and that notifications are only created for the intended recipient.

### Issue 5: The last song in a playlist never shows up

#### How I reproduced it

I reproduced this bug using the existing test case in `tests/test_playlists.py`, specifically `test_playlist_returns_all_songs`.

The test creates a playlist with 5 songs, then calls:

```python
songs = get_playlist_songs(playlist_id)
```

The expected result is that `len(songs)` should be `5`, because the playlist contains 5 songs. The buggy behavior was that only 4 songs were returned. That confirmed the problem happened when retrieving the songs for a playlist: the playlist data had 5 songs, but the service response dropped one.

#### How I found the root cause

I started in `routes/playlists.py` because the symptom is about viewing playlist songs. The route for that feature is:

```python
@playlists_bp.route("/<playlist_id>/songs")
def get_songs(playlist_id):
```

That route calls:

```python
songs = get_playlist_songs(playlist_id)
```

So I opened `services/playlist_service.py` and inspected `get_playlist_songs()`.

The function queries songs through the `playlist_entries` table and sorts them by:

```python
.order_by(asc(playlist_entries.c.position))
```

That part made sense because playlist order is stored in `playlist_entries.position`.

The suspicious part was the return statement:

```python
return [song.to_dict() for song in songs[:-1]]
```

I checked what `songs[:-1]` means in Python, and it means “return everything except the last item.” That matched the symptom exactly: a playlist with 5 queried songs would return only the first 4.

#### The root cause

The root cause was an incorrect list slice in `get_playlist_songs()`.

The database query correctly fetched the playlist songs in order, but the function returned:

```python
songs[:-1]
```

Instead of:

```python
songs
```

In Python, `songs[:-1]` removes the final item from the list. So no matter how many songs were in the playlist, the last one was always left out of the response. The issue was not with the database join or playlist ordering; the final song was dropped after the query, during serialization.

#### My fix and side-effect check

I fixed the bug by changing the return statement from:

```python
return [song.to_dict() for song in songs[:-1]]
```

to:

```python
return [song.to_dict() for song in songs]
```

This fixes the root cause because the service now serializes every song returned by the ordered query.

For the side-effect check, I verified:

- A playlist with 5 songs now returns all 5 songs.
- The songs are still returned in playlist position order.
- An empty playlist still returns an empty list.
- The fix does not change playlist metadata retrieval, because `get_playlist()` is a separate function.
- The fix does not affect playlist creation, because `create_playlist()` is separate from retrieval.

That check matters because the bug was at the final serialization step. Returning `songs` instead of `songs[:-1]` restores the missing item while preserving the query and ordering behavior.

## Regression Test

I added `tests/test_feed.py` for Issue 2, specifically `test_listening_now_excludes_friend_outside_recent_window()`. The test creates one current user with two friends: one friend listened 10 minutes ago, and the other listened 45 minutes ago. Friends Listening Now should include the 10-minute friend and exclude the 45-minute friend.

This test would have failed against the buggy code because `RECENT_THRESHOLD` was set to `timedelta(hours=24)`. With a 24-hour cutoff, the friend who listened 45 minutes ago still counted as "listening now." After changing the threshold to 30 minutes, the test verifies both sides of the boundary.

I also added `test_activity_feed_still_includes_older_friend_activity()` to confirm the general activity feed still includes older friend activity. That check matters because Issue 2 should only narrow Friends Listening Now, not remove older events from the separate activity feed.
