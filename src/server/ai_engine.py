"""五子棋 AI (纯标准库). 供服务端人机对战调用."""

from __future__ import annotations

import random
from typing import List, Optional, Tuple

from src.server.room import BOARD_SIZE, BLACK, DIRECTIONS, EMPTY, WHITE

AI_NAME = "电脑"
VALID_LEVELS = ("easy", "normal", "hard")


def _check_win(board: List[List[int]], row: int, col: int, color: int) -> bool:
    for dr, dc in DIRECTIONS:
        cnt = 1
        r, c = row + dr, col + dc
        while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and board[r][c] == color:
            cnt += 1
            r += dr
            c += dc
        r, c = row - dr, col - dc
        while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and board[r][c] == color:
            cnt += 1
            r -= dr
            c -= dc
        if cnt >= 5:
            return True
    return False


def _line_score(length: int, open_ends: int) -> int:
    if length >= 5:
        return 1_000_000
    if length == 4:
        return 50_000 if open_ends == 2 else 8_000
    if length == 3:
        return 3_000 if open_ends == 2 else 400
    if length == 2:
        return 80 if open_ends == 2 else 10
    if length == 1:
        return 4 if open_ends == 2 else 1
    return 0


def _cell_line_score(board: List[List[int]], row: int, col: int, dr: int, dc: int, color: int) -> int:
    length = 1
    open_ends = 0
    r, c = row + dr, col + dc
    while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and board[r][c] == color:
        length += 1
        r += dr
        c += dc
    if 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and board[r][c] == EMPTY:
        open_ends += 1
    r, c = row - dr, col - dc
    while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and board[r][c] == color:
        length += 1
        r -= dr
        c -= dc
    if 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and board[r][c] == EMPTY:
        open_ends += 1
    return _line_score(length, open_ends)


def _evaluate_board(board: List[List[int]], color: int) -> int:
    opp = WHITE if color == BLACK else BLACK
    score = 0
    mid = BOARD_SIZE // 2
    for r in range(BOARD_SIZE):
        for c in range(BOARD_SIZE):
            if board[r][c] != EMPTY:
                continue
            dist = abs(r - mid) + abs(c - mid)
            score -= dist
            for dr, dc in DIRECTIONS:
                # 假设在此落子后的进攻/防守潜力
                board[r][c] = color
                score += _cell_line_score(board, r, c, dr, dc, color) * 2
                board[r][c] = opp
                score += _cell_line_score(board, r, c, dr, dc, opp)
                board[r][c] = EMPTY
    return score


def _find_critical_move(board: List[List[int]], color: int) -> Optional[Tuple[int, int]]:
    """能一手取胜则取胜, 否则挡对手一手胜."""
    for r in range(BOARD_SIZE):
        for c in range(BOARD_SIZE):
            if board[r][c] != EMPTY:
                continue
            board[r][c] = color
            if _check_win(board, r, c, color):
                board[r][c] = EMPTY
                return r, c
            board[r][c] = EMPTY
    opp = WHITE if color == BLACK else BLACK
    for r in range(BOARD_SIZE):
        for c in range(BOARD_SIZE):
            if board[r][c] != EMPTY:
                continue
            board[r][c] = opp
            if _check_win(board, r, c, opp):
                board[r][c] = EMPTY
                return r, c
            board[r][c] = EMPTY
    return None


def _candidate_moves(board: List[List[int]]) -> List[Tuple[int, int]]:
    stones = [(r, c) for r in range(BOARD_SIZE) for c in range(BOARD_SIZE) if board[r][c] != EMPTY]
    if not stones:
        mid = BOARD_SIZE // 2
        return [(mid, mid)]
    cand: set[Tuple[int, int]] = set()
    for r, c in stones:
        for dr in range(-2, 3):
            for dc in range(-2, 3):
                nr, nc = r + dr, c + dc
                if 0 <= nr < BOARD_SIZE and 0 <= nc < BOARD_SIZE and board[nr][nc] == EMPTY:
                    cand.add((nr, nc))
    return list(cand) or [(BOARD_SIZE // 2, BOARD_SIZE // 2)]


def _score_move(board: List[List[int]], row: int, col: int, color: int) -> int:
    board[row][col] = color
    total = 0
    for dr, dc in DIRECTIONS:
        total += _cell_line_score(board, row, col, dr, dc, color) * 3
    opp = WHITE if color == BLACK else BLACK
    board[row][col] = opp
    for dr, dc in DIRECTIONS:
        total += _cell_line_score(board, row, col, dr, dc, opp) * 2
    board[row][col] = EMPTY
    mid = BOARD_SIZE // 2
    total -= (abs(row - mid) + abs(col - mid))
    return total


def _best_heuristic_move(board: List[List[int]], color: int) -> Tuple[int, int]:
    crit = _find_critical_move(board, color)
    if crit:
        return crit
    best: Optional[Tuple[int, int]] = None
    best_score = -10**18
    for r, c in _candidate_moves(board):
        s = _score_move(board, r, c, color)
        if s > best_score:
            best_score = s
            best = (r, c)
    return best or (BOARD_SIZE // 2, BOARD_SIZE // 2)


def _board_winner(board: List[List[int]]) -> int:
    for r in range(BOARD_SIZE):
        for c in range(BOARD_SIZE):
            color = board[r][c]
            if color != EMPTY and _check_win(board, r, c, color):
                return color
    return 0


def _minimax(
    board: List[List[int]],
    depth: int,
    turn: int,
    me: int,
    alpha: int,
    beta: int,
) -> int:
    winner = _board_winner(board)
    if winner == me:
        return 10**6 + depth
    if winner and winner != me:
        return -10**6 - depth
    if depth == 0:
        return _evaluate_board(board, me)
    opp = WHITE if turn == BLACK else BLACK
    moves = _candidate_moves(board)[:14]
    if turn == me:
        val = -10**18
        for r, c in moves:
            board[r][c] = turn
            val = max(val, _minimax(board, depth - 1, opp, me, alpha, beta))
            board[r][c] = EMPTY
            alpha = max(alpha, val)
            if beta <= alpha:
                break
        return val
    val = 10**18
    for r, c in moves:
        board[r][c] = turn
        val = min(val, _minimax(board, depth - 1, opp, me, alpha, beta))
        board[r][c] = EMPTY
        beta = min(beta, val)
        if beta <= alpha:
            break
    return val


def _minimax_move(board: List[List[int]], color: int, depth: int) -> Tuple[int, int]:
    crit = _find_critical_move(board, color)
    if crit:
        return crit
    best_move = _best_heuristic_move(board, color)
    best_val = -10**18
    for r, c in _candidate_moves(board)[:14]:
        board[r][c] = color
        if _check_win(board, r, c, color):
            board[r][c] = EMPTY
            return r, c
        val = _minimax(board, depth - 1, WHITE if color == BLACK else BLACK, color, -10**18, 10**18)
        board[r][c] = EMPTY
        if val > best_val:
            best_val = val
            best_move = (r, c)
    return best_move


def choose_move(board: List[List[int]], color: int, level: str = "normal") -> Tuple[int, int]:
    level = level if level in VALID_LEVELS else "normal"
    crit = _find_critical_move(board, color)
    if crit:
        return crit
    if level == "easy":
        moves = _candidate_moves(board)
        if random.random() < 0.35:
            return random.choice(moves)
        scored = [( _score_move(board, r, c, color), (r, c)) for r, c in moves]
        scored.sort(reverse=True)
        top = scored[: max(3, len(scored) // 4)]
        return random.choice([m for _, m in top])
    if level == "hard":
        return _minimax_move(board, color, depth=3)
    return _best_heuristic_move(board, color)
