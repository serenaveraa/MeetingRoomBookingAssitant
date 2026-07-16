# AI Agent Case Study: ODC Meeting Room Booking Assistant

## Business Context

The ODC has only one common meeting room that is shared by all associates. Due to heavy demand, associates often face booking conflicts, meeting overruns, and lack of visibility into room availability.

Build an AI-powered Meeting Room Booking Agent that helps associates efficiently reserve and manage the single ODC meeting room.

## Problem Statement

Design and develop an intelligent AI Agent that:

- Allows associates to book the ODC meeting room using natural language
- Displays available time slots
- Prevents overlapping bookings
- Sends reminders before meeting completion
- Manages extensions and conflicts
- Provides room utilization insights

## Functional Requirements

### 1. Room Availability Check

**User asks:**

> Can I book the meeting room today from 2 PM to 3 PM?

**Agent should:**

- Check occupancy calendar
- Show availability
- Confirm booking

### 2. Natural Language Booking

**Examples:**

- "Book room tomorrow for 30 minutes"
- "Reserve room for client discussion from 4 PM to 5 PM"

**AI should identify:**

- Date
- Start time
- End time
- Meeting purpose

### 3. Conflict Detection

If the slot is already reserved, the agent should respond:

> Room is already booked from 2 PM to 3 PM.
>
> Available alternatives:
> - 1 PM – 2 PM
> - 3 PM – 4 PM
> - 4 PM – 5 PM

### 4. 15-Minute Vacate Notification

**Example:**

| | |
|---|---|
| Current booking | 10:00 AM – 11:00 AM |
| Next booking | 11:00 AM – 12:00 PM |

At **10:45 AM**, the AI sends:

> Your meeting will end in 15 minutes.
> Another meeting is scheduled immediately after yours.
> Kindly vacate the room.

### 5. Meeting Extension Request

**User:**

> Extend my meeting by 30 minutes

**Agent should:**

- **If no booking exists** — approve automatically
- **If another booking exists** — reject and notify:

> Extension not possible.
> Room reserved by Associate XYZ starting at 11:00 AM.

### 6. Booking Cancellation

**User:**

> Cancel my booking

**Agent should:**

- Release slot
- Notify waitlisted users

## Expected Technology Stack

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI |
| AI Layer | LangChain / LangGraph, OpenAI / Azure OpenAI |
| Database | SQLite / PostgreSQL |
| Notification Service | Email, Teams Notification |
| Frontend | Streamlit |
