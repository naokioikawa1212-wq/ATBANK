#!/bin/bash
set -e

echo "=== 動画自動カット アプリ起動 ==="

# Start backend
cd backend
uvicorn main:app --reload --port 8000 &
BACKEND_PID=$!
cd ..

# Start frontend dev server
cd frontend
npm run dev &
FRONTEND_PID=$!
cd ..

echo ""
echo "バックエンド: http://localhost:8000"
echo "フロントエンド: http://localhost:5173"
echo ""
echo "Ctrl+C で停止"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
wait
