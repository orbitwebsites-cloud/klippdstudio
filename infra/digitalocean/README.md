# DigitalOcean deployment

Run the `Deploy backend to DigitalOcean` workflow manually from GitHub Actions. The first run creates a `s-2vcpu-4gb` Ubuntu Droplet, uploads the checked-out repository source and backend configuration, and runs the FastAPI/FFmpeg image. Future runs reuse the same named Droplet and redeploy the latest `main` branch.

Required repository secrets:

- `DIGITALOCEAN_ACCESS_TOKEN`
- `DROPLET_SSH_PRIVATE_KEY`
- `MONGO_URL`
- `MASTER_ENCRYPTION_KEY`

Optional secrets: `SEED_GROQ_KEY` and `SEED_CEREBRAS_KEY`.

The workflow deliberately has no `push` trigger. It creates a paid Droplet, even when DigitalOcean credits cover the invoice. After the first run, set a DNS A record for `api.klippdstudio.com` to the workflow output IP, put HTTPS in front of it, and set the frontend `REACT_APP_BACKEND_URL` to that URL.
