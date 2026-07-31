CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(64) UNIQUE NOT NULL,
    password_hash VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    dimension_weights JSONB DEFAULT '{}'::jsonb,
    negative_feedback_history JSONB DEFAULT '[]'::jsonb,
    verb_preferences JSONB DEFAULT '{}'::jsonb,
    cultural_preferences JSONB DEFAULT '{}'::jsonb,
    agency VARCHAR(16) DEFAULT 'balanced',
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS poem_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(255),
    target_dimensions JSONB DEFAULT '{}'::jsonb,
    current_node_id UUID,
    is_public BOOLEAN DEFAULT FALSE,
    stage VARCHAR(32) DEFAULT 'chat',
    chat_log JSONB DEFAULT '[]'::jsonb,
    source_session_id UUID,
    canvas_json JSONB DEFAULT '{}'::jsonb,
    run_token VARCHAR(64),
    run_status VARCHAR(32) DEFAULT 'idle',
    checkpoint_id VARCHAR(64),
    soft_ask_skips INT DEFAULT 0,
    stage_meta JSONB DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE poem_sessions ADD COLUMN IF NOT EXISTS canvas_json JSONB DEFAULT '{}'::jsonb;
ALTER TABLE poem_sessions ADD COLUMN IF NOT EXISTS run_token VARCHAR(64);
ALTER TABLE poem_sessions ADD COLUMN IF NOT EXISTS run_status VARCHAR(32) DEFAULT 'idle';
ALTER TABLE poem_sessions ADD COLUMN IF NOT EXISTS checkpoint_id VARCHAR(64);
ALTER TABLE poem_sessions ADD COLUMN IF NOT EXISTS soft_ask_skips INT DEFAULT 0;
ALTER TABLE poem_sessions ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE poem_sessions ADD COLUMN IF NOT EXISTS stage_meta JSONB DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS poem_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES poem_sessions(id) ON DELETE CASCADE,
    parent_id UUID REFERENCES poem_nodes(id),
    ai_thought TEXT,
    poem_content TEXT,
    radar_scores JSONB DEFAULT '{}'::jsonb,
    is_executed BOOLEAN DEFAULT FALSE,
    stage VARCHAR(32),
    canvas_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE poem_nodes ADD COLUMN IF NOT EXISTS canvas_json JSONB DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_poem_nodes_parent ON poem_nodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_poem_nodes_session ON poem_nodes(session_id);
CREATE INDEX IF NOT EXISTS idx_poem_sessions_public ON poem_sessions(is_public);
