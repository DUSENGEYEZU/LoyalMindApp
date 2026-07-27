# Patches the exact-case string "KoboToolbox" -> "LoyalMinds" in the already
# -compiled frontend bundle and server-rendered templates of the official kpi
# image. No source fork, no webpack rebuild - just a text substitution over
# build artifacts that already exist in the base image.
#
# Deliberately does NOT touch the lowercase "kobotoolbox" that also appears in
# these files - that's real external stuff, not display text:
#   - help@kobotoolbox.org           (support fallback email)
#   - github.com/kobotoolbox/kpi     (bug report links)
#   - webpackChunk_kobotoolbox_kpi   (internal JS chunk-loader variable name -
#                                     renaming this breaks script loading)
#
# Rebuild this image whenever KPI_BASE_TAG is bumped to match a new upstream
# kobotoolbox/kpi version (see the `image:` line in
# kobo-docker/docker-compose.frontend.yml for the currently pinned tag).
ARG KPI_BASE_TAG=2.026.27b
FROM kobotoolbox/kpi:${KPI_BASE_TAG}

RUN find /srv/src/kpi/staticfiles/compiled -type f \( -name '*.js' -o -name '*.js.map' \) \
      -exec sed -i 's/KoboToolbox/LoyalMinds/g' {} + \
 && find /srv/src/kpi/kpi/templates /srv/src/kpi/kobo/apps -type f -name '*.html' \
      -exec sed -i 's/KoboToolbox/LoyalMinds/g' {} +

# kobologo.svg is the login-page logo AND the after-login top-bar logo (same
# file, four identical on-disk copies at different static paths) - it has no
# editable text, every letter is an outlined vector shape, so a plain sed
# can't touch it. Swap in a real wordmark instead (see kobologo.svg in this
# directory for what changed and what was deliberately kept, i.e. the icon).
COPY kobologo.svg /tmp/loyalminds-logo.svg
RUN cp /tmp/loyalminds-logo.svg /srv/src/kpi/staticfiles/kobologo.svg \
 && cp /tmp/loyalminds-logo.svg /srv/src/kpi/staticfiles/img/kobologo.svg \
 && cp /tmp/loyalminds-logo.svg /srv/src/kpi/staticfiles/compiled/kobologo.svg \
 && cp /tmp/loyalminds-logo.svg /srv/src/kpi/staticfiles/kobocat/images/kobologo.svg \
 && rm /tmp/loyalminds-logo.svg
