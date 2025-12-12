# Movie Recommendation System — README

## Overview
This project implements a fully functional movie-recommendation system using:
- **Neo4j** as the primary graph database  
- **Redis + RediSearch** for caching and full-text search  
- **Python 3** as the application layer integrating both systems  

The system supports:
- Importing movie data into Neo4j  
- User–based collaborative filtering to generate recommendations  
- Redis caching for fast repeated recommendation queries  
- Full-text movie search using RediSearch  
- A complete menu-driven user application (profile management, rating movies, retrieving updated recommendations, etc.)  

A grader can run the provided Python script to reproduce *all* results demonstrated in the report.  
This README explains exactly how to install dependencies, configure databases, and run the demo.

---

## 1. Language, Version, and Dependencies:
**Language:** Python 3.10+  
**Required Packages:**
- `neo4j` (Neo4j Python driver)
- `redis`
- `redisearch` (if using the older client) or `redis.commands.search` (for Redis-Stack)
- `json`
- `typing`

The Python file already imports everything needed.

**Databases Required:**
- **Neo4j Desktop or AuraDB** (local installation recommended)
- **Redis-Stack Server**

---

## 2. Installation Instructions:

### Step 1 — Install Neo4j
Download Neo4j Desktop or install Neo4j Community Edition from:  
https://neo4j.com/download/

Required configuration:
- Enable the Bolt server (default bolt://localhost:7687)
- Set a password for the `neo4j` user  
- Place your CSV files inside the `import/` directory  
- Update memory settings as needed for large imports

### Step 2 — Install Redis-Stack
Redis-Stack is required because the project uses **RediSearch**.

Install from:  
https://redis.io/docs/latest/operate/oss_and_stack/install/install-stack/

Start Redis-Stack server:
```bash
redis-stack-server
```
### Step 3 — Install Python Dependencies
In your project directory:
```
pip install neo4j redis
```
### 3. How to Run the Program:

### Step 1 — Start Required Services
You must start Redis-Stack and Neo4j before running the Python script.
- Terminal 1:
```
redis-stack-server
```
- Terminal 2:
Start Neo4j Desktop → Start your database that has the CSV file imported

### Step 2 — Run the Python Program
First, update your Neo4j and Redis connection parameters to matach your databasae information (make sure your password for Neo4j is correct)
From VS Code or terminal:
```
python3 redis_movie_integration_complete.py
```
The program will:
- Connect to Neo4j
- Load movie data into Redis
- Build the RediSearch index
- Display the main menu

### 4. Program Usage Guide (User Manual):
After launching the program, the user sees the main menu:

Main Menu Options
1. Load movies into Redis
   - Extracts movies from Neo4j
   - Stores each movie as a Redis hash using movie:<movieID>
   Rebuilds the RediSearch index
2. Search movies (full-text search)
   - Uses RediSearch to query titles, genres, and year
   - Returns matching movie metadata
3. Get recommendations (with Redis cache)
   - Computes user-based collaborative filtering recommendations
   - Key format: *recs:user:<userId>:k:<k>*
   - Cached results returned instantly
   - Cache automatically refreshes if parameters change
4. View all cached recommendation keys
   - Lists Redis keys matching *recs:user:*:k:**
5 User Application Mode
Provides a multi-feature user experience:

Create a new user profile

Log in to an existing profile

Search movies (with personalized rating info)

Rate movies

Retrieve Top-5 recommended unseen movies

Automatically updates:

Movie avgRating

User rating history

Redis cache and hashes

All changes persist across operations

Exit the application

Safely closes Redis and Neo4j connections

### 5. Demonstration Workflow (What the Grader Should Do): 
To reproduce all results shown in the report:

Start Neo4j and Redis-Stack

Run the Python script

From the menu:

Run Option 1 to load movies

Run Option 2 (search) to verify RediSearch is working

Run Option 3 twice with the same userId and k to show cache hit

Run Option 4 to display the cached keys

Run Option 5:

Create a new user

Search for movies

Rate a movie

Request new Top-5 recommendations (they will change based on new ratings)

Exit using Option 0

This confirms:

Graph-based recommendation logic works

Redis caching works

Full text search works

User profile + rating persistence works

All updated values appear correctly in Redis

### 6. Notes for Reproducibility:
The program will not run unless both Neo4j and Redis-Stack are active.

Ensure the Neo4j credentials inside the script match your installation.

CSV import into Neo4j must be completed before running the Python script.

If RediSearch index already exists, the script handles it safely.

### 7. File Summary:
redis_movie_integration_complete.py

The main executable script containing:

Neo4j integration

Redis caching and full-text search

Collaborative filtering recommendation logic

User-profile and rating management

Menu-driven CLI application

project_mdb.docx

Your full written report documenting:

Data model

Recommendation strategy

Redis integration

User application features

Screenshots and verification steps

### 8. Support & Troubleshooting:
If the script prints connection errors:

Verify Redis-Stack is running on port 6379

Verify Neo4j Bolt port is 7687

Update credentials inside the script as necessary

Delete stale RediSearch indexes using:

FT.DROPINDEX id:movie KEEPDOCS
