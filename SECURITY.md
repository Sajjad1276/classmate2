# Security

- Never commit `.env`, bot tokens, or database passwords.
- `BOOTSTRAP_ADMIN_IDS` are the only users who become platform admin at runtime.
- Group link codes expire in 30 minutes and are stored hashed.
- Student join codes expire in 7 days and are stored hashed.
- Submitters cannot approve their own records.
- Only approved records appear in normal list commands.
