#!/bin/sh
# Test users and groups in the development directory (compose profile "auth"), from users/*.json
# and groups/*.json. Run inside the lldap container; running it again changes nothing:
#   docker compose exec lldap sh /bootstrap/bootstrap.sh
set -eu

api=http://localhost:17170
token=$(curl -sf "$api/auth/simple/login" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$LLDAP_LDAP_USER_DN\",\"password\":\"$LLDAP_LDAP_USER_PASS\"}" | jq -r .token)

gql() {
  curl -sf "$api/api/graphql" -H "Authorization: Bearer $token" -H 'Content-Type: application/json' \
    -d "$(jq -n --arg q "$1" --argjson v "$2" '{query: $q, variables: $v}')"
}

group_id() {
  gql 'query { groups { id displayName } }' '{}' |
    jq -r --arg n "$1" '.data.groups[] | select(.displayName == $n) | .id'
}

for f in /bootstrap/groups/*.json; do
  name=$(jq -r .name "$f")
  [ -n "$(group_id "$name")" ] ||
    gql 'mutation ($n: String!) { createGroup(name: $n) { id } }' "$(jq -n --arg n "$name" '{n: $n}')" >/dev/null
done

for f in /bootstrap/users/*.json; do
  id=$(jq -r .id "$f")
  gql 'mutation ($u: CreateUserInput!) { createUser(user: $u) { id } }' \
    "$(jq '{u: {id, email, displayName, firstName, lastName}}' "$f")" >/dev/null || true
  /app/lldap_set_password --base-url "$api" --admin-username "$LLDAP_LDAP_USER_DN" \
    --admin-password "$LLDAP_LDAP_USER_PASS" --username "$id" --password "$(jq -r .password "$f")" >/dev/null
  for g in $(jq -r '.groups[]' "$f"); do
    gql 'mutation ($u: String!, $g: Int!) { addUserToGroup(userId: $u, groupId: $g) { ok } }' \
      "$(jq -n --arg u "$id" --argjson g "$(group_id "$g")" '{u: $u, g: $g}')" >/dev/null
  done
  echo "user $id"
done
