# google_signin

Google-account sign-in for Cloud Run services, the same way the Operator Console does it:
Cloud Run direct IAP (no load balancer) decides who gets past Google's login page, and the
service verifies IAP's signed header to learn who it is. Python twin of the console's
`lib/auth/identity.ts` and `lib/access/iap.ts`.

| Module | Use |
|---|---|
| `identity.py` | `email_from_headers(request.headers, audience("<service>"))` → the verified email, or `NotSignedIn` |
| `access.py` | List, grant, revoke or copy the Google accounts allowed to sign in to a service |

## Put a service behind Google sign-in

```bash
gcloud run deploy <service> ... --no-allow-unauthenticated --iap
gcloud run services add-iam-policy-binding <service> --region us-central1 \
  --member serviceAccount:service-887772634501@gcp-sa-iap.iam.gserviceaccount.com --role roles/run.invoker
python3 -m skills.google_signin.access grant --service <service> --email someone@example.com
```

The project's OAuth consent screen and IAP client already exist from the console's one-time
setup (`RUNBOOK.md` §17), so a new service needs no Cloud Console clicks.

Local dev: `BYPASS_IAP_EMAIL=you@example.com` stands in for the signed header.

## A signed-in front end that proxies to its own API

Google's front end drops `x-goog-*` headers on a request entering another Cloud Run service, so
the proxy re-sends the assertion as `X-Signin-Assertion` (nginx:
`proxy_set_header X-Signin-Assertion $http_x_goog_iap_jwt_assertion;`). `email_from_headers`
reads either header and verifies the token against the **front end's** audience. The API image
needs `google-auth>=2.37` and `pyjwt[crypto]` (IAP keys are ES256 JWKs).
