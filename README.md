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

## 1. Language, Version, and Dependencies
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
- **Redis-Stack Server** (NOT plain Redis)
- **RedisInsight** (optional, for visualization)

---

## 2. Installation Instructions

### Step 1 — Install Neo4j
Download Neo4j Desktop or install Neo4j Community Edition from:  
https://neo4j.com/download/

Required configuration:
- Enable the Bolt server (default bolt://localhost:7687)
- Set a password for the `neo4j` user  
- Place your CSV files inside the `import/` directory  
- Update memory settings as needed for large imports (as you did)

### Step 2 — Install Redis-Stack
Redis-Stack is required because the project uses **RediSearch**.

Install from:  
https://redis.io/docs/latest/operate/oss_and_stack/install/install-stack/

Start Redis-Stack server:
```bash
redis-stack-server
