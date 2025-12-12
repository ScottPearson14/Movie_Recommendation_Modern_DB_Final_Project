"""
# File Description:

Modern Database Final Project: 
Part 3 & Part 4 – Integration with Redis + Neo4j

Primary DB: Neo4j
Cache + Search: Redis (Redis-Stack with RediSearch)

This script implements:

PART 3:
  1) Load movies from Neo4j into Redis as HASHes.
  2) Create a RediSearch index over movies (title + genres + year + avg_rating).
  3) Full-text search over movies using Redis.
  4) Cache top-K movie recommendations per user in Redis
     (results originally computed with a Neo4j Cypher query).

PART 4 (new user application requirements):
  1) Prompt the user to enter their user ID.
     - If the user exists in Neo4j, greet them by name.
     - Otherwise, prompt for a name and create the user node.
  2) Retrieve the list of movies the user has rated from Neo4j,
     cache them in Redis with an expiration time, and explain the TTL choice.
  3) Support movie title search in Redis using full-text search, showing:
       title, genre(s), average rating, whether the user has seen it,
       and the user's rating (if applicable).
  4) Display the top 5 recommended movies the user has not seen or rated.
  5) Allow the user to submit a rating for any recommended movie.
     Persist the new rating in Neo4j and update the average rating in Redis.
  6) Ensure all database connections are properly closed before exit.
"""

# import statements
from __future__ import annotations
import json  
from typing import List, Dict, Any
import redis                   
from redis.commands.search.query import Query 
from neo4j import GraphDatabase 
from redis.commands.search.field import TextField, NumericField
from redis.commands.search.index_definition import IndexDefinition, IndexType



# Neo 4j connection parameters
NEO4J_URI = "neo4j://127.0.0.1:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "moderndatabasefinal" # Specify your password here
NEO4J_DATABASE = "moviesprimary" # Specify the target database name

# Redis connection parameters
REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_DB = 0


def get_neo4j_driver():
    """
    Create and return a Neo4j driver instance.
    """
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    return driver


def get_redis_client():
    """
    Create and return a Redis client instance.
    """
    r = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        decode_responses=True, # to get strings instead of bytes to ensure compatibility with JSON
    )
    
    # Quick ping to verify connection
    print("Redis PING ->", r.ping())
    return r


############### PART 3 – Redis integration helpers (movies + recommendations): ###############

def load_movies_from_neo4j_into_redis(r: redis.Redis, driver) -> None:
    """
    Read all Movie nodes from Neo4j and store them as Redis HASHes.

    Expected Neo4j schema:

        (:Movie {
            movieId: INTEGER or STRING,
            title:   STRING,
            genres:  STRING,   # e.g. 'Action|Adventure'
            year:    INTEGER
        })

    Redis key pattern:
        movie:<movieId>

    Stored fields:
        movie_id, title, genres, year
    """

    # Cypher query to get all movies
    cypher = """
    MATCH (m:Movie)
    RETURN m.movieId AS movie_id,
           m.title   AS title,
           m.genres  AS genres,
           m.year    AS year
    ORDER BY movie_id
    """

    # Execute the Cypher query and store results in Redis
    print("Loading movies from Neo4j into Redis...") # Debug print
    
    # Use the specified database for the session
    with driver.session(database=NEO4J_DATABASE) as session:
        # Run the query
        result = session.run(cypher)

        # Count of loaded movies
        count = 0
        # For each movie record, create a Redis HASH
        for record in result:
            # Extract fields
            movie_id = record["movie_id"]
            title = record["title"] or ""
            genres_value = record["genres"] or []

            # genres is stored as a LIST in Neo4j, so we join into one string for Redis
            # Handle case where genres is a list
            if isinstance(genres_value, list):
                genres = " | ".join(genres_value)   # e.g. "Animation | Children's | Comedy"

            # Handle case where genres is a single string
            else:
                genres = str(genres_value)
            
            # Handle null year
            year = record["year"] if record["year"] is not None else 0

            # Redis key
            key = f"movie:{movie_id}"

            # Store as HASH
            r.hset(
                key,
                mapping={
                    "movie_id": str(movie_id),
                    "title": title,
                    "genres": genres,
                    "year": str(year),
                },
            )
            # Increment count
            count += 1

    print(f"Loaded {count} movie hashes into Redis.") # Debug print


def create_redis_search_index(r):
    """
    Create a RediSearch index on the movie HASHes.

    Index name: idx:movie
    Prefix: "movie:"

    Fields:
      - title       : TEXT
      - genres      : TEXT
      - year        : NUMERIC
      - avg_rating  : NUMERIC (sortable)

    If the index already exists, we drop it and recreate it.
    """
    # Create RediSearch index
    try:
        ft = r.ft("idx:movie")

        # Try to drop existing index if present (no document deletion).
        try:
            ft.dropindex(delete_documents=False)
            print("Existing index 'idx:movie' dropped.")
        
        # If index does not exist, ignore the error.
        except Exception:
            # No index to drop, that's fine.
            pass

        # Define the index schema
        ft.create_index(
            (
                TextField("title"),
                TextField("genres"),
                NumericField("year"),
                NumericField("avg_rating", sortable=True),
            ),
            definition=IndexDefinition(
                prefix=["movie:"],
                index_type=IndexType.HASH,
            ),
        )

        print("RediSearch index 'idx:movie' created.\n") # Debug print

    # Handle any errors during index creation
    except Exception as e:
        print(f"Error creating RediSearch index: {e}\n")


def redis_fulltext_search_demo(r):
    """
    Part 3 full-text search demo.

    Behavior:
      - Ask user for a RediSearch query string (e.g. 'star war*', '@genres:Comedy').
      - Ask how many results to show (max 10).
      - Run the query exactly as typed.
      - Print total hits and show up to N movies with movie_id, year, title, genres.
    """

    # Prompt user for search term
    term = input("Enter search query (e.g. star war*, @genres:Comedy, etc.): ").strip()
    # Handle empty input
    if not term:
        print("Empty search, returning.\n")
        return

    # Ask user how many results to return (max 10)
    max_str = input("Max number of results [10]: ").strip()
    # Default to 10 if empty or invalid
    if not max_str:
        max_results = 10
    
    # Try to parse integer
    else:
        try:
            max_results = int(max_str)
        except ValueError:
            max_results = 10

    # Clamp between 1 and 10
    if max_results < 1:
        max_results = 1
    if max_results > 10:
        max_results = 10

    # Get the RediSearch index
    try:
        ft = r.ft("idx:movie")
    
    # Handle error if index does not exist
    except Exception as e:
        print(f"Error opening index 'idx:movie': {e}")
        return

    # DO NOT alter the user query; user controls wildcards and syntax.
    # We just run it as-is.
    query_str = term
    query = Query(query_str).paging(0, max_results)

    # Execute the search
    try:
        res = ft.search(query)

    # Handle search errors
    except Exception as e:
        print(f"Error executing search: {e}")
        return

    print(f"\nQuery '{query_str}' → {res.total} total hits, returning {len(res.docs)} movie(s).\n") # Debug print

    # If no results, inform the user
    if res.total == 0 or len(res.docs) == 0:
        print("No results.\n")
        return

    print("Search results:") # Debug print

    # Print each result
    for doc in res.docs:
        movie_id = getattr(doc, "movie_id", "(none)")
        year = getattr(doc, "year", "(none)")
        title = getattr(doc, "title", "(no title)")
        genres = getattr(doc, "genres", "(no genres)")

        print(f"    movie_id={movie_id}, year={year}, title={title}, genres={genres}") # Debug print
    print()


def query_top_k_recommendations_from_neo4j(driver, user_id: int, k: int) -> List[Dict[str, Any]]:
    """
    Compute top-K movie recommendations for a user using Neo4j.

    This is the same collaborative-filter style query that returns a 
    list of dicts:

        {"movie_id": <int>, "predicted_rating": <float>}

    Assumed Neo4j graph:

      (:User {userId: <int>})-[:RATED {rating: <float>}]->(:Movie)
    """

    # Cypher query for top-K recommendations
    cypher = """
    // 1) Find the target user
    MATCH (u:User {userId: $user_id})

    // 2) Find other users who rated the same movies
    MATCH (u)-[:RATED]->(m:Movie)<-[:RATED]-(other:User)
    WHERE other <> u

    // 3) Measure similarity by number of common rated movies
    WITH u, other, count(*) AS common_rated
    ORDER BY common_rated DESC
    LIMIT 50

    // 4) From similar users, gather candidate movies they rated
    MATCH (other)-[r:RATED]->(rec:Movie)
    WHERE NOT (u)-[:RATED]->(rec)       // user hasn't rated rec yet

    // 5) Predicted score as average rating among similar users
    WITH rec, avg(r.rating) AS predicted
    RETURN rec.movieId AS movie_id, predicted
    ORDER BY predicted DESC
    LIMIT $k
    """

    # Execute the Cypher query against the moviesprimary DB
    with driver.session(database=NEO4J_DATABASE) as session:
        # Run the query with parameters
        result = session.run(cypher, user_id=user_id, k=k)

        # Extract results into list of dicts
        recs = [
            {
                "movie_id": record["movie_id"],
                "predicted_rating": float(record["predicted"]),
            }
            for record in result
        ]

    return recs


def get_recommendations_for_user(driver, r, user_id: int, k: int = 10, ttl_seconds: int = 600,) -> List[Dict[str, Any]]:
    """
    Part 3 + Part 4 helper:

    - Uses the same Neo4j CF-style query as Part 3
      (via query_top_k_recommendations_from_neo4j).
    - Caches per-user top-K recommendations in Redis.

    Redis key:
      recs:user:<user_id>:k:<k>

    Value stored in Redis:
      JSON list of {"movie_id": ..., "predicted_rating": ...}

    TTL (ttl_seconds) controls how long the recommendations stay in cache.
    """
    # Generate Redis cache key
    cache_key = f"recs:user:{user_id}:k:{k}"

    # 1) Try cache first
    cached = r.get(cache_key)
    # Cache hit
    if cached is not None:
        print(f"[CACHE HIT] {cache_key}") # Debug print
        # Handle both bytes and str just in case
        if isinstance(cached, (bytes, bytearray)):
            cached_str = cached.decode("utf-8")

        # If already a string
        else:
            cached_str = str(cached)

        # Parse JSON
        try:
            return json.loads(cached_str)
        except json.JSONDecodeError:
            # If cache content is corrupted, ignore and recompute
            pass

    # 2) Cache miss: compute recommendations from Neo4j
    print(f"[CACHE MISS] {cache_key} -> querying Neo4j...") # Debug print
    recs = query_top_k_recommendations_from_neo4j(driver, user_id, k)

    # 3) Store in Redis with TTL
    r.setex(cache_key, ttl_seconds, json.dumps(recs))

    return recs



################## PART 4 – User application helpers ##########################

def prompt_for_user_id() -> int:
    """
    Prompt the user for their numeric user ID and return it as an int.
    Keeps asking until a valid integer is entered.
    """
    # Loop until valid input
    while True:
        raw = input("Enter your user ID: ").strip()

        # Try to convert to int
        try:
            return int(raw)
        
        # Handle invalid input
        except ValueError:
            print("User ID must be an integer. Please try again.\n")


def get_or_create_user(session, user_id: int) -> str:
    """
    Look up a User node by userId. If it exists, return the name.
    If it exists but has no name, prompt for one and update the node.
    If it does not exist, create it safely (respecting the uniqueness
    constraint on :User(userId)).
    """
    # 1) Try to find an existing user with this userId
    result = session.run(
        """
        MATCH (u:User {userId: $user_id})
        RETURN u.name AS name
        """,
        user_id=user_id,
    )
    # Get single record (if any)
    record = result.single()

    # Check if user exists
    if record is not None:
        existing_name = record["name"]

        # Case A: user exists and already has a name
        if existing_name is not None:
            print(f"\nWelcome back, {existing_name}!\n")
            return existing_name

        # Case B: user exists but name is missing -> ask once and set it
        name = input(
            "We found your user ID but no name is stored yet.\n"
            "Please enter your name: "
        ).strip()
        # Default name if empty
        if not name:
            name = f"User{user_id}"

        # Update the name in Neo4j
        session.run(
            """
            MATCH (u:User {userId: $user_id})
            SET u.name = $name
            """,
            user_id=user_id,
            name=name,
        )

        print(f"\nWelcome, {name}! Your profile has been updated.\n") # Debug print
        return name

    # 2) No node with this userId yet -> create it safely using MERGE
    name = input("No user found. Please enter your name: ").strip()
    # Default name if empty
    if not name:
        name = f"User{user_id}"

    # Create the user node
    session.run(
        """
        MERGE (u:User {userId: $user_id})
        ON CREATE SET u.name = $name
        """,
        user_id=user_id,
        name=name,
    )

    print(f"\nWelcome, {name}! Your profile has been created.\n") # Debug print
    return name



def get_user_rated_movies(session, user_id: int) -> List[Dict[str, Any]]:
    """
    Retrieve the list of movies the user has rated from Neo4j.
    """
    # Cypher query to get rated movies
    result = session.run(
        """
        MATCH (u:User {userId: $user_id})-[r:RATED]->(m:Movie)
        RETURN m.movieId AS movie_id,
               m.title   AS title,
               r.rating  AS rating
        ORDER BY r.rating DESC, title
        """,
        user_id=user_id,
    )

    movies: List[Dict[str, Any]] = []
    # Extract results
    for record in result:
        movies.append(
            {
                "movie_id": str(record["movie_id"]),
                "title": record["title"],
                "rating": float(record["rating"]),
            }
        )
    return movies


def cache_user_rated_movies(r, user_id: int, rated_movies: List[Dict[str, Any]], ttl_seconds: int = 3600):
    """
    Cache the user's rated movies in Redis.

    We store:
      1) A HASH "user:<id>:ratings" mapping movie_id -> rating
         (fast membership / rating lookup).
      2) A STRING "user:<id>:ratings:list" with the JSON list of
         {movie_id, title, rating} (for quickly printing all ratings).

    TTL justification:
      I decided to use 1 hour (3600 seconds) as a balance between performance
      and freshness. User ratings do not usually change very often,
      but we might need to query them many times during a short
      interactive session. Caching them for an hour avoids repeated
      Neo4j queries in the same session, while still allowing the
      cache to expire and be refreshed later if new ratings are added.
    """
    # Build the HASH mapping movie_id -> rating
    hash_key = f"user:{user_id}:ratings"

    # Store in Redis HASH
    if rated_movies:
        mapping = {m["movie_id"]: m["rating"] for m in rated_movies}
        r.hset(hash_key, mapping=mapping)
        r.expire(hash_key, ttl_seconds)

    else:
        # If no movies rated, ensure the hash is cleared
        r.delete(hash_key)

    # Store a JSON-encoded list for convenience
    list_key = f"user:{user_id}:ratings:list"
    r.set(list_key, json.dumps(rated_movies), ex=ttl_seconds)


def get_cached_user_ratings(r, user_id: int) -> Dict[str, float]:
    """
    Get the cached ratings as a dict movie_id -> rating.
    Returns an empty dict if there is no cache.
    """
    # Retrieve from Redis HASH
    hash_key = f"user:{user_id}:ratings"
    data = r.hgetall(hash_key)
    # Redis returns strings; convert rating values to float
    return {movie_id: float(rating) for movie_id, rating in data.items()}


def search_movies_with_user_context(r, user_id: int, user_ratings: Dict[str, float]):
    """
    Prompt the user for a search string and perform a RediSearch query
    over movies. For each result (max 10), display:
      - title
      - genres
      - average rating
      - whether the user has seen it
      - the user's rating (if applicable)

    The search term is applied only to the `title` field, using a
    RediSearch query like:
        @title:term
    or, for multi-word terms:
        @title:(multi word term)

    The user can include wildcards or other RediSearch syntax in
    the term if desired (e.g., "star war*", "comedy|drama").
    """

    # Prompt for search term
    search_term = input("Enter part of a movie title to search for: ").strip()
    # Handle empty input
    if not search_term:
        print("Empty search; returning to menu.\n") # Debug print
        return

    # Get the RediSearch index
    try:
        ft = r.ft("idx:movie")

    # Handle error if index does not exist
    except Exception as e:
        print(f"Error opening index 'idx:movie': {e}")
        return

    # Build query string restricted to the title field.
    # I do NOT auto-append '*' so the user has full control.
    # If there are spaces, wrap in parentheses: @title:(star war*)

    # Check for spaces
    if " " in search_term:
        query_str = f"@title:({search_term})"

    # No spaces
    else:
        query_str = f"@title:{search_term}"

    # Limit to top 10 results
    query = Query(query_str).paging(0, 10)

    # Execute the search
    try:
        res = ft.search(query)

    # Handle search errors
    except Exception as e:
        print(f"Error executing search: {e}")
        return

    # If no results, inform the user
    if res.total == 0:
        print("No movies matched your search.\n")
        return

    print(f"\nTop {min(10, res.total)} results for \"{search_term}\":\n") # Debug print

    # Print each result with user context
    for doc in res.docs:
        # Extract movie ID from Redis key
        redis_id = doc.id
        # Our HASH keys are like "movie:<movieId>"
        if redis_id.startswith("movie:"):
            movie_id = redis_id.split("movie:", 1)[1]

        # Fallback if unexpected format
        else:
            movie_id = redis_id

        # Extract other fields
        title = getattr(doc, "title", "(no title)")
        genres = getattr(doc, "genres", "(no genres)")
        avg_rating = getattr(doc, "avg_rating", None)

        # Determine if the user has seen this movie
        has_seen = movie_id in user_ratings
        # Get user's rating if available
        user_rating_str = f"{user_ratings[movie_id]:.1f}" if has_seen else "N/A"

        # Format average rating
        avg_rating_str = "N/A"

        # Try to format avg_rating as float with 2 decimal places
        if avg_rating is not None:
            # Handle possible conversion errors
            try:
                avg_rating_str = f"{float(avg_rating):.2f}"

            # If conversion fails, just use the raw value
            except (TypeError, ValueError):
                avg_rating_str = str(avg_rating)

        # Print the movie info
        print(f"  Movie ID     : {movie_id}")
        print(f"  Title      : {title}")
        print(f"  Genres     : {genres}")
        print(f"  Avg Rating : {avg_rating_str}")
        print(f"  Seen       : {'YES' if has_seen else 'NO'}")
        print(f"  Your rating: {user_rating_str}")
        print()

    print()



def display_top_5_recommendations(driver, r, user_id: int, user_ratings: Dict[str, float],) -> List[Dict[str, Any]]:
    """
    Get the top 5 recommended movies for the user (using Part 3 logic),
    filter out movies the user has already rated, look up the movie
    titles from Redis, display them, and return the final list of
    recommendations (for rating later).
    """
    # Get more than 5 in case many have already been seen
    all_recs = get_recommendations_for_user(driver, r, user_id, k=20)
    # Filter out already rated movies
    if not all_recs:
        print("No recommendations available.\n")
        return []

    # Filter out movies already rated by this user
    unseen_recs = [
        rec for rec in all_recs
        if str(rec["movie_id"]) not in user_ratings
    ]
    # Take top 5 unseen
    top_5 = unseen_recs[:5]

    # If none left, inform the user
    # This probably means the user has rated almost everything which would take a super long time lol
    if not top_5:
        print("You have already rated all recommended movies.\n")
        return []

    # Enrich each recommendation with the movie title (and keep it
    # stored in the dict so option 3 can reuse it).
    for rec in top_5:
        # Look up title from Redis
        movie_id = rec["movie_id"]
        redis_key = f"movie:{movie_id}"

        # Try to get title
        try:
            title = r.hget(redis_key, "title")

        # Handle any Redis errors
        except Exception:
            title = None

        # Fallback if title not found
        if not title:
            title = "(no title)"

        rec["title"] = title  # store for later use (rate_movie)

    print("\nTop 5 recommended movies you have not rated yet:\n") # Debug print

    # Print each recommendation
    for idx, rec in enumerate(top_5, start=1):
        # Extract fields
        movie_id = rec["movie_id"]
        title = rec.get("title", "(no title)")
        score = rec.get("predicted_rating", None)

        # Format line
        line = f"{idx}. [{movie_id}] {title}"
        # Append predicted score if available
        if score is not None:
            # Try to format score as float with 4 decimal places
            try:
                score_val = float(score)
                line += f"  (recommender score: {score_val:.4f})"

            # Handle possible conversion errors
            except (TypeError, ValueError):
                line += f"  (recommender score: {score})"

        print(line)
    print()

    return top_5


def rate_movie(driver, r, user_id: int, recommendations: List[Dict[str, Any]], user_ratings: Dict[str, float],):
    """
    Allow the user to submit a rating for any movie in the provided
    `recommendations` list.

    After rating:
      - Store rating in Neo4j.
      - Recompute average rating for the movie in Neo4j and update Redis.
      - Update the user's cached rating hash in Redis and in-memory dict.
    """
    # If no recommendations, nothing to rate
    if not recommendations:
        print("No recommendations available to rate.\n")
        return

    # Ask which movie to rate
    choice = input(
        "Enter the number of the movie you want to rate (or press Enter to cancel): "
    ).strip()
    if not choice:
        print("Rating cancelled.\n")
        return

    # Validate choice
    try:
        idx = int(choice)
    except ValueError:
        print("Invalid choice. Rating cancelled.\n")
        return

    # Check range
    if idx < 1 or idx > len(recommendations):
        print("Choice out of range. Rating cancelled.\n")
        return

    # Get the selected movie
    movie = recommendations[idx - 1]

    # Extract movie ID and look up the title from Redis if needed
    movie_id = str(movie["movie_id"])
    title = movie.get("title")

    # If title not present, look it up from Redis
    if not title or title == "(no title)":
        # Look up title from Redis
        redis_key = f"movie:{movie_id}"
        try:
            title_from_redis = r.hget(redis_key, "title")

        # Handle any Redis errors
        except Exception:
            title_from_redis = None

        # Fallback if title not found
        if title_from_redis:
            title = title_from_redis
            movie["title"] = title   # keep it in the list as well

        # Final fallback
        else:
            title = "(no title)"

    # Ask for rating value
    rating_str = input(f"Enter your rating for '{title}' (0.5 - 5.0): ").strip()
    # Validate rating
    try:
        rating_val = float(rating_str)

    # Handle invalid float conversion
    except ValueError:
        print("Invalid rating. Rating cancelled.\n")
        return

    # Check rating range
    if not (0.5 <= rating_val <= 5.0):
        print("Rating must be between 0.5 and 5.0. Rating cancelled.\n")
        return

    # 1) Store new rating in Neo4j
    with driver.session(database=NEO4J_DATABASE) as session:
        # Create or update the RATED relationship
        session.run(
            """
            MATCH (u:User {userId: $user_id}), (m:Movie {movieId: $movie_id})
            MERGE (u)-[r:RATED]->(m)
            SET r.rating  = $rating,
                r.ratedAt = datetime()
            """,
            user_id=user_id,
            movie_id=int(movie_id),
            rating=rating_val,
        )

        # 2) Recompute average rating from primary DB
        avg_result = session.run(
            """
            MATCH (:User)-[r:RATED]->(m:Movie {movieId: $movie_id})
            RETURN avg(r.rating) AS avg_rating
            """,
            movie_id=int(movie_id),
        )
        avg_record = avg_result.single()
        new_avg = avg_record["avg_rating"] if avg_record is not None else None

    # 3) Update average rating in Redis movie hash
    redis_key = f"movie:{movie_id}"
    if new_avg is not None:
        try:
            r.hset(redis_key, mapping={"avg_rating": float(new_avg)})
        except Exception as e:
            print(f"Warning: failed to update avg_rating in Redis: {e}")

    # 4) Update user's rating cache in Redis and in-memory dict
    hash_key = f"user:{user_id}:ratings"
    r.hset(hash_key, movie_id, rating_val)
    # Optionally, extend TTL if desired:
    # r.expire(hash_key, 3600)

    user_ratings[movie_id] = rating_val

    # Confirmation message
    print(f"\nYou rated '{title}' (movie {movie_id}) with {rating_val:.1f} stars.")
    if new_avg is not None:
        print(f"New average rating for this movie is {float(new_avg):.2f}.\n")

    # If average rating could not be recomputed, so inform the user
    else:
        print("Average rating could not be recomputed.\n")


def run_user_application(driver, r):
    """
    Main Part 4 user-facing application.

    Steps:
      1. Prompt for user ID and get/create user in Neo4j.
      2. Load user ratings from Neo4j and cache them in Redis.
      3. Interactive menu:
         - Search movies (full-text search in Redis with user context)
         - Show top-5 recommendations (user has not rated)
         - Rate a recommended movie
         - Exit back to the main menu
    """
    # 1) User login / creation
    user_id = prompt_for_user_id()
    # Get or create user in Neo4j
    with driver.session(database=NEO4J_DATABASE) as session:
        user_name = get_or_create_user(session, user_id)

        # 2) Get rated movies and cache them
        rated_movies = get_user_rated_movies(session, user_id)
    # Cache in Redis with TTL
    cache_user_rated_movies(r, user_id, rated_movies)
    # Load cached ratings into in-memory dict
    user_ratings = get_cached_user_ratings(r, user_id)

    # Keep the latest shown recommendations so we can rate them
    last_recommendations: List[Dict[str, Any]] = []

    # 3) Menu loop
    while True:
        print("=== Part 4 – User Application Menu ===")
        print("1. Search movies by title (Redis full-text + user context)")
        print("2. Show top-5 recommended movies you haven't rated")
        print("3. Rate a movie from the latest recommendations")
        print("0. Return to main menu")
        choice = input("Choose an option: ").strip()

        if choice == "1":
            search_movies_with_user_context(r, user_id, user_ratings)
        elif choice == "2":
            last_recommendations = display_top_5_recommendations(driver, r, user_id, user_ratings)
        elif choice == "3":
            if not last_recommendations:
                print("No recommendations loaded yet. Choose option 2 first.\n")
            else:
                rate_movie(driver, r, user_id, last_recommendations, user_ratings)
        elif choice == "0":
            print("Returning to main menu...\n")
            break
        else:
            print("Invalid choice, try again.\n")


# Main menu combining Part 3 and Part 4
def main():
    """
    Top-level menu that combines the original Part 3 options
    with the new Part 4 user application.
    """
    driver = get_neo4j_driver()
    r = get_redis_client()

    # Main menu loop
    try:
        # Loop until user chooses to exit
        while True:
            print("=== Main Menu ===")
            print("1. Load movies from Neo4j into Redis (Part 3)")
            print("2. Create / recreate RediSearch index over movies (Part 3)")
            print("3. Simple Redis full-text search demo (Part 3)")
            print("4. Show cached recommendations for a user (Part 3)")
            print("5. Run user application (Part 4)")
            print("0. Exit")
            choice = input("Choose an option: ").strip()

            if choice == "1":
                load_movies_from_neo4j_into_redis(r, driver)
            elif choice == "2":
                create_redis_search_index(r)
            elif choice == "3":
                redis_fulltext_search_demo(r)
            elif choice == "4":
                # Part 3-style recommendations with Redis cache
                try:
                    user_id = int(input("Enter user ID for recommendations: ").strip())
                except ValueError:
                    print("User ID must be an integer.\n")
                    continue

                max_str = input("How many recommendations? [10]: ").strip()
                if not max_str:
                    k = 10
                else:
                    try:
                        k = int(max_str)
                    except ValueError:
                        k = 10

                # Clamp between 1 and 10
                if k < 1:
                    k = 1
                if k > 10:
                    k = 10

                recs = get_recommendations_for_user(driver, r, user_id, k=k)
                if not recs:
                    print("\nNo recommendations.\n")
                else:
                    print(f"\nRecommendations for user {user_id}:")
                    for rec in recs:
                        print(f"    movie_id={rec['movie_id']}, predicted_rating={rec['predicted_rating']:.2f}")
                    print()


            elif choice == "5":
                # New Part 4 user application
                run_user_application(driver, r)
            elif choice == "0":
                print("Exiting.")
                break
            else:
                print("Invalid choice, try again.\n")
    # Ensure proper cleanup
    finally:
        # Ensure all database connections are properly closed (Part 4 – requirement 6)
        try:
            driver.close()
            print("Neo4j driver closed.")
        except Exception:
            pass

        # Redis-py does not strictly require a close(), but call it if present
        close_fn = getattr(r, "close", None)
        if callable(close_fn):
            try:
                close_fn()
                print("Redis client closed.")
            except Exception:
                pass


if __name__ == "__main__":
    main()