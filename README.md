# AI Agent Business Process Automation

## Project Overview
This project implements a LangGraph-based AI agent that automates competitor price and feature monitoring for a B2B SaaS business.

The agent replaces the manual process where analysts monitor competitor pricing pages, compare changes, judge business impact, and prepare reports for Sales/Product teams.

## Agent Workflow

Competitor Page
↓
Web Fetch Tool
↓
Extract Pricing Data
↓
Snapshot Database Comparison
↓
Change Classification
↓
Human Approval (for material changes)
↓
Generate Report


## Agent Capabilities

- Multi-step reasoning using ReAct pattern
- External information retrieval
- Data extraction and comparison
- Snapshot-based change detection
- Human-in-the-loop approval workflow


## Framework Used

LangGraph

Reason:
- Explicit state management
- Traceable execution steps
- Human approval checkpoints
- Suitable for business workflows


## Tools Used

1. Web Fetch Tool
   - Retrieves competitor pricing information

2. Snapshot Store Tool
   - Stores previous competitor states for comparison

3. Code Execution Tool
   - Extracts fields and calculates differences


## Files

| File | Description |
|---|---|
| AI Agent Prototype.py | Working agent prototype |
| Framework Justification.md | LangGraph vs AutoGen vs CrewAI comparison |
| Agent Execution Traces and Evaluation.md | ReAct traces and 10-task evaluation |
| Design and Evaluation Report.md | Final design and evaluation report |


## How to Run

Install dependencies:

pip install langgraph langchain-anthropic

Run:

python AI_Agent_Prototype.py


## Evaluation Summary

The agent was tested on 10 competitor monitoring scenarios.

Results:
- Trajectory Quality: 4.1/5
- Output Quality: 3.3/5

Main failures:
- Schema mismatch
- Data extraction errors
- Missing confidence validation


## Future Improvements

- Real web browsing integration
- Better extraction models
- Confidence scoring
- Production database integration
