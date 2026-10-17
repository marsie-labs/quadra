# Quadra

Real-time team workspace for quadrant-based decisions (IS, IS NOT, DOES, DOES NOT).
The app is a NiceGUI page plus the `POST /sessions` API on one FastAPI app, backed by Supabase.

## Run

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

1. Create a `.env` file in the project root:

   ```
   SUPABASE_URL=https://<project>.supabase.co
   SUPABASE_PUBLISHABLE_KEY=<publishable (anon) key>
   SUPABASE_DB_URL=postgresql://...   # only for the tests, which apply the migration
   ```

   The app needs `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`.
   Apply `supabase/migrations/0001_workspace.sql` to the database once.

2. Start the app (`--env-file` loads `.env`):

   ```
   uv run uvicorn quadra.app:app --env-file .env
   ```

3. Create a session, then open the `link` it returns:

   ```
   curl -X POST http://127.0.0.1:8000/sessions \
     -H 'Content-Type: application/json' -d '{"feature_name": "My feature"}'
   ```

   Open `http://127.0.0.1:8000/sessions/<session_id>` in a browser to see the canvas.

## Test

```
uv run pytest
```
