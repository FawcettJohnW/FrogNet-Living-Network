FrogNet PHP API (entity/action router)

Files:
- config.php — DB connection (defaults: db=FrogNet, user=FrogUser, pass=<set at install>)
- db.php — PDO connection helper
- entities.php — map API entities to tables, columns and PKs
- util.php — JSON I/O, query builders, helpers
- api.php — main endpoint (call as http://host/path/api.php?entity=sensors&action=list)

Supported actions:
- list (GET): filters Field=value, Field__like=value, order=Col [ASC|DESC], limit, offset
- get (GET): require PK(s) as query params
- create (POST JSON): body with required fields
- update (PUT JSON): body must include PK(s); updates other provided fields
- delete (DELETE): PK(s) in query params

Response:
  { "success": true, "rows": [...], "count": N }
  { "success": true, "row": {...} }
  { "success": true, "insert_id": 7, "row": {...} }
  { "success": true, "deleted": 1 }
  { "success": false, "error": "message", "detail": "..."} 

Deploy:
  - Copy to your web root (e.g., /var/www/html/frognet_api/)
  - Ensure pdo_mysql is enabled in PHP
  - Adjust config.php for DB host/user/pass if needed
