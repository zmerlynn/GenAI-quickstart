## NPC Chat API

> [!NOTE]
> Work in progress!

The NPC Chat API demonstrates using [RAG](https://www.promptingguide.ai/techniques/rag) to create a 
smart NPC for your game, enriched by world knowledge, NPC-specific knowledge, and secondhand
knowledge from chat.

To try it out:

* First start the API server and forward `svc/npc-chat-api` locally:

```
cd $CUR_DIR/genai/api/npc_chat_api
skaffold run
kubectl port-forward -n genai svc/npc-chat-api 7777:80
```

* Seed the world knowledge by posting to the `/reset_world_data` endpoint. You can use this
endpoint at any time to flush previous chat history and start over from the original state.

```
curl -XPOST "http://localhost:7777/reset_world_data"
```

* Send a chat!

```
curl -XPOST "http://localhost:7777/" -H 'accept: application/json' -H 'Content-Type: application/json' -d '{"message": "What happened here?"}'

# Add the "debug" flag if you'd like to see diagnostic data. Pipe it to `jq` to pretty print.
# curl -XPOST "http://localhost:7777/" -H 'accept: application/json' -H 'Content-Type: application/json' -d '{"message": "What happened here?", "debug": true}' | jq
```

TODO:

* The API does not take an ID for the target of the message and assumes you're talking to Joseph (EntityId = 1 in the database)
* The API does not take an ID for the player and instead assumes the player is Jane (EntityId = 2 in the database)
* The config and world data is loaded into the Dockerfile instead of being loaded through a ConfigMap (or something else k8s-native)
* Currently all GenAI is handled through the VertexAI API. This should be swapped out to use the endpoints in this repo so that we can remain somewhat agnostic to the backend.
