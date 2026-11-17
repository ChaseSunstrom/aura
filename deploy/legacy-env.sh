# Sourced (POSIX sh) after the host's .env, by the deploy scripts and boot-recover.sh (T-0959).
# Env names say AURA_, but that .env (which the repo may not rewrite) and GitLab's project
# variables still say JARVIS_: each exported JARVIS_X is exported as AURA_X unless AURA_X is
# set, so the new name always wins. Only names reach eval (they match [A-Za-z0-9_]); a value
# is expanded inside double quotes, never re-parsed. deploy/entrypoint.sh does the same in the
# image. Does not cover a variable that is set but not exported.
for _k in $(env | sed -n 's/^\(JARVIS_[A-Za-z0-9_]*\)=.*/\1/p'); do
  _n="AURA_${_k#JARVIS_}"
  eval "[ -n \"\${$_n+x}\" ] || export $_n=\"\${$_k}\""
done
unset _k _n
