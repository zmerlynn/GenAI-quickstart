from google.cloud import spanner

def db_from_config(cfg, genai):
    if cfg['global']['database'] == 'Spanner':
        return Spanner(genai, cfg['global'], cfg['Spanner'])
    raise Exception(f"Unknown database config: {cfg['global']['database']}")

class Spanner(object):
    #TODO: These are pretty similar tables, maybe refactor together?
    _INSERT_BASE = """\
INSERT INTO EntityHistoryBase(EntityId, EventId, EntityName, EntityType, EventDescription, EventDescriptionEmbedding) VALUES
(@entityId, (SELECT IFNULL(MAX(EventId), 0)+1 FROM EntityHistoryBase), @entityName, @entityType, @eventDescription, @embedding)
"""

    _INSERT_DYNAMIC = """\
INSERT INTO EntityHistoryDynamic(EntityId, EventId, EventTime, EntityName, EntityType, TargetEntityId, TargetEntityName, EventDescription, EventDescriptionEmbedding) VALUES
(@entityId, (SELECT IFNULL(MAX(EventId), 10000)+1 FROM EntityHistoryDynamic), PENDING_COMMIT_TIMESTAMP(), @entityName, @entityType, @targetEntityId, @targetEntityName, @eventDescription, @embedding)
"""

    _CHAT_HISTORY = """\
SELECT
  EntityId,
  EventDescription
FROM EntityHistoryDynamic
WHERE 
  (EntityId = @entityId1 AND TargetEntityId = @entityId2) OR (EntityId = @entityId2 AND TargetEntityId = @entityId1)
ORDER BY EventId DESC
LIMIT @limit
"""

    _KNOWLEDGE = """
WITH maybeRelevant AS (
    -- maybeRelevant is a union of "known facts" by @entityId, and world facts (entity 0),
    -- plus anything @entityId said or heard in chat. The `Provenance` column indicates who
    -- relayed the fact, with NULL meaning "It is known".
    SELECT
        EventDescription,
        NULL as Provenance,
        EventDescriptionEmbedding,
        COSINE_DISTANCE(EventDescriptionEmbedding, @embedding) as Distance
    FROM EntityHistoryBase
    WHERE EntityId = 0 OR EntityId = @entityId
    UNION ALL
    SELECT
        EventDescription,
        IF(EntityId = @entityId, "I", EntityName) as Provenance,
        EventDescriptionEmbedding,
        COSINE_DISTANCE(EventDescriptionEmbedding, @embedding) as Distance
    FROM EntityHistoryDynamic
    WHERE EntityId = @entityId OR TargetEntityId = @entityId
), withinDistance AS (
    -- withinDistance filters the relevant pieces down to a maximum distance and structures
    -- the event for bucketing, below.    
    SELECT
        STRUCT(EventDescription as EventDescription, Provenance as Provenance) as Data,
        Distance
    FROM maybeRelevant
    WHERE Distance < @distance
), bucketed AS (
    -- bucketed implements a form of "minimum distance" anti-crowding: we partition the
    -- results up into buckets of size 1/@crowdBuckets, e.g. 0.1, and pick an arbitrary one
    -- from each bucket. This helps the diversity of knowledge returned.
    SELECT
        ANY_VALUE(Data) as Data,
        ANY_VALUE(Distance) as Distance
    FROM withinDistance
    GROUP BY CAST(Distance * @crowdBuckets AS INT64)
)
SELECT
  Data.EventDescription as EventDescription,
  Data.Provenance as Provenance,
  Distance
FROM bucketed
ORDER BY Distance
LIMIT @limit
"""

    def __init__(self, genai, gcfg, cfg):
        self._get_embeddings = genai.get_embeddings
        self._db = spanner.Client().instance(cfg['instance_id']).database(cfg['database_id'])

    def _insert_base(self, txn, base_event):
        descs = base_event['events']
        embeddings = self._get_embeddings(descs)

        for (desc, embedding) in zip(descs, embeddings):
            txn.execute_update(
                self._INSERT_BASE,
                params={
                    'entityId': base_event['entity_id'],
                    'entityName': base_event['entity_name'],
                    'entityType': base_event['entity_type'],
                    'eventDescription': desc,
                    'embedding': embedding,
                },
                param_types={
                    'entityId': spanner.param_types.INT64,
                    'entityName': spanner.param_types.STRING,
                    'entityType': spanner.param_types.INT64,
                    'eventDescription': spanner.param_types.STRING,
                    'embedding': spanner.param_types.Array(spanner.param_types.FLOAT64),
                }
            )

    def _insert_chat(self, txn, chat_event):
        descs = chat_event['chat_history']
        embeddings = self._get_embeddings(descs)
        speakers = ((chat_event['entity_id'], chat_event['entity_name']), (chat_event['target_entity_id'], chat_event['target_entity_name']))

        for (i, desc, embedding) in zip(range(len(descs)), descs, embeddings):
            source, target = speakers[i % 2], speakers[(i+1) % 2] # alternate who is speaking
            txn.execute_update(
                self._INSERT_DYNAMIC,
                params={
                    'entityId': source[0],
                    'entityName': source[1],
                    'entityType': chat_event['entity_type'],
                    'targetEntityId': target[0],
                    'targetEntityName': target[1],
                    'eventDescription': desc,
                    'embedding': embedding,
                },
                param_types={
                    'entityId': spanner.param_types.INT64,
                    'entityName': spanner.param_types.STRING,
                    'targetEntityId': spanner.param_types.INT64,
                    'targetEntityName': spanner.param_types.STRING,
                    'eventType': spanner.param_types.INT64,
                    'eventDescription': spanner.param_types.STRING,
                    'embedding': spanner.param_types.Array(spanner.param_types.FLOAT64),
                },
            )

    def get_chat_history(self, entity_id1, entity_id2, limit):
        with self._db.snapshot() as snapshot:
            rows = snapshot.execute_sql(
                self._CHAT_HISTORY,
                params={
                    'entityId1': entity_id1,
                    'entityId2': entity_id2,
                    'limit': limit,
                },
                param_types={
                    'entityId1': spanner.param_types.INT64,
                    'entityId2': spanner.param_types.INT64,
                    'limit': spanner.param_types.INT64,
                },
            )
            return list([{'entity_id': row[0], 'message': row[1]} for row in reversed(list(rows))])

    def insert_chat(self, entity_id, entity_name, target_entity_id, target_entity_name, messages):
        def _insert_in_txn(txn):
            self._insert_chat(txn, {
                'entity_id': entity_id,
                'entity_name': entity_name,
                'entity_type': 1, # NPC
                'target_entity_id': target_entity_id,
                'target_entity_name': target_entity_name,
                'chat_history': messages,
            })
        self._db.run_in_transaction(_insert_in_txn)

    def get_knowledge(self, entity_id, embedding, distance, limit, crowd_buckets=10):
        with self._db.snapshot() as snapshot:
            knowledge = snapshot.execute_sql(
                self._KNOWLEDGE,
                params={
                    'entityId': entity_id,
                    'embedding': embedding,
                    'distance': distance,
                    'limit': limit,
                    'crowdBuckets': crowd_buckets,
                },
                param_types={
                    'entityId': spanner.param_types.INT64,
                    'embedding': spanner.param_types.Array(spanner.param_types.FLOAT64),
                    'distance': spanner.param_types.FLOAT64,
                    'limit': spanner.param_types.INT64,
                    'crowdBuckets': spanner.param_types.INT64,
                },
            )
            return [{'knowledge': row[0], 'provenance': row[1], 'distance': row[2]} for row in knowledge]

    def reinitialize(self, data):
        def _reinit_in_txn(txn):
            txn.execute_update('DELETE EntityHistoryBase WHERE True')
            for base_event in data['base']:
                self._insert_base(txn, base_event)

            txn.execute_update(f'DELETE EntityHistoryDynamic WHERE True')
            for chat_event in data['chat']:
                self._insert_chat(txn, chat_event)
        self._db.run_in_transaction(_reinit_in_txn)
