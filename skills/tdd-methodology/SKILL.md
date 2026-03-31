# Vertical Slice TDD

A methodology for building complete features from database to UI using Test-Driven Development.

## Core Principle

Every feature is a **vertical slice** through all layers:

```
Component -> Hook -> Route -> Service -> Schema
    |          |       |        |          |
  Test       Test    Test     Test       Test
```

Each layer has its own test. No layer is implemented without a test first.

## TDD Cycle: Red-Green-Refactor

### 1. RED - Write a Failing Test
Write a test that describes what you want the code to do. Run it — it MUST fail.

### 2. GREEN - Make It Pass
Write the MINIMUM code needed to make the test pass. Nothing more.

### 3. REFACTOR - Clean Up
Improve the code without changing behavior. Tests must still pass.

## Layer Responsibilities

### Schema (Database)
- Drizzle schema definitions
- Migrations
- Always include `tenantId` for multi-tenancy
- Always include timestamps

### Service (Business Logic)
- Pure business logic
- Takes input, returns output
- Always filters by `tenantId`

### Route (API)
- Next.js API route handlers
- Input validation (Zod)
- Authentication middleware
- Calls service layer

### Hook (Data Fetching)
- TanStack Query hooks
- Query keys for caching
- Optimistic updates
- Error handling

### Component (UI)
- React components
- Uses hooks for data
- Handles loading/error states
- Uses shadcn/ui primitives

## Available Skills

| Skill | Purpose |
|-------|---------|
| `/feature <name>` | Start new feature with TDD |
| `/refactor <name>` | Start refactoring with TDD |
| `/ship` | Validate, commit, push, create PR |
| `/quickship` | Push directly to main |
| `/merge` | Squash-merge PR into main |
| `/validate` | Check slice completeness |
