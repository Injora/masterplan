# YouTube Shorts Factory

YouTube Shorts Factory is an automated content generation platform for creating, clipping, and publishing YouTube Shorts. It uses AI video generation and automated scheduling to publish content across multiple YouTube channels.

## Project Structure

The project is divided into two main parts:
- `backend/`: A FastAPI application that handles video clipping, AI story/video generation, channel management, and upload scheduling. It also integrates with `ngrok` for webhooks or external access.
- `frontend/`: A Next.js application (React 19, Tailwind CSS v4) that serves as the dashboard and UI for managing content, channels, and schedules.

## Features
- **Automated Shorts Clipping**: Clip existing videos into Shorts format.
- **AI Video Generation**: Generate short stories and videos automatically using the story engine.
- **Multi-channel Publishing**: Manage multiple YouTube channels and queue uploads.
- **Background Scheduler**: Process upload queues and generate content automatically.
- **Dashboard**: Monitor system health, view generated clips, and manage schedules from the Next.js frontend.

## Getting Started

### Backend Setup
1. Navigate to `backend/` and install dependencies.
2. Configure your environment variables in `.env` (use `.env.example` as a template).
3. Start the backend:
   ```bash
   cd backend
   python main.py
   ```
   *or*
   ```bash
   uvicorn main:app --reload --host 0.0.0.0 --port 8000
   ```

### Frontend Setup
1. Navigate to `frontend/` and install dependencies:
   ```bash
   cd frontend
   npm install
   ```
2. Start the development server:
   ```bash
   npm run dev
   ```

### Manual Story Generation
You can manually run a story generation and queue it for upload using the provided script at the root:
```bash
python run_story_manual.py
```
