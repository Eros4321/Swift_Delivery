# Restoring staging administrator access

The deployment command `ensure_deployment_admin` can create a staging Django
administrator or restore an existing user's staff, superuser, active, email,
and password settings. It does nothing unless explicitly enabled.

## One-time Render configuration

Add these variables only to the staging web service:

```env
DJANGO_ADMIN_SETUP_ENABLED=True
DJANGO_ADMIN_USERNAME=your-staging-admin-username
DJANGO_ADMIN_EMAIL=your-email@example.com
DJANGO_ADMIN_PASSWORD=use-a-strong-unique-staging-password
```

The build script runs the command after migrations. A successful deployment
prints one of these messages without exposing credentials:

```text
Deployment administrator created successfully.
Deployment administrator restored successfully.
```

After confirming that staging admin login works:

1. Set `DJANGO_ADMIN_SETUP_ENABLED=False`.
2. Delete `DJANGO_ADMIN_PASSWORD` from Render.
3. Save the environment changes without exposing their values.

The command remains safe on future deployments because it exits without
changing any user whenever the setup flag is disabled.
