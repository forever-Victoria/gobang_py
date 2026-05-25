"""服务端五子棋 AI.

AI 只根据服务端权威棋盘计算下一步, 不直接修改房间状态。
策略优先级:
1. 自己一步能赢就直接赢;
2. 对手一步能赢就堵住;
3. 对所有空位做局部连子评分, 选择分数最高的位置。
"""

from __future__ import annotations

from typing import Optional

from src.server.room import BLACK, BOARD_SIZE, EMPTY, WHITE

DIRECTIONS = ((0, 1), (1, 0), (1, 1), (1, -1))


class GobangAI:
    def choose_move(self, board: list[list[int]], color: int) -> Optional[tuple[int, int]]:
        opponent = BLACK if color == WHITE else WHITE
        candidates = self._candidates(board)
        if not candidates:
            return None

        for row, col in candidates:
            if self._would_win(board, row, col, color):
                return row, col
        for row, col in candidates:
            if self._would_win(board, row, col, opponent):
                return row, col

        center = BOARD_SIZE // 2
        best_move = None
        best_score = -1
        for row, col in candidates:
            score = self._score_move(board, row, col, color)
            score += int(self._score_move(board, row, col, opponent) * 0.85)
            score += max(0, 14 - (abs(row - center) + abs(col - center)))
            if score > best_score:
                best_score = score
                best_move = (row, col)
        return best_move

    def _candidates(self, board: list[list[int]]) -> list[tuple[int, int]]:
        stones = [
            (r, c)
            for r in range(BOARD_SIZE)
            for c in range(BOARD_SIZE)
            if board[r][c] != EMPTY
        ]
        if not stones:
            center = BOARD_SIZE // 2
            return [(center, center)]

        seen = set()
        for r, c in stones:
            for nr in range(max(0, r - 2), min(BOARD_SIZE, r + 3)):
                for nc in range(max(0, c - 2), min(BOARD_SIZE, c + 3)):
                    if board[nr][nc] == EMPTY:
                        seen.add((nr, nc))
        center = BOARD_SIZE // 2
        return sorted(seen, key=lambda p: abs(p[0] - center) + abs(p[1] - center))

    def _would_win(self, board: list[list[int]], row: int, col: int, color: int) -> bool:
        for dr, dc in DIRECTIONS:
            count = 1
            count += self._count(board, row, col, dr, dc, color)
            count += self._count(board, row, col, -dr, -dc, color)
            if count >= 5:
                return True
        return False

    def _score_move(self, board: list[list[int]], row: int, col: int, color: int) -> int:
        score = 0
        for dr, dc in DIRECTIONS:
            forward = self._count(board, row, col, dr, dc, color)
            backward = self._count(board, row, col, -dr, -dc, color)
            line = 1 + forward + backward
            open_ends = 0
            if self._is_open(board, row + (forward + 1) * dr, col + (forward + 1) * dc):
                open_ends += 1
            if self._is_open(board, row - (backward + 1) * dr, col - (backward + 1) * dc):
                open_ends += 1
            score += self._line_score(line, open_ends)
        return score

    @staticmethod
    def _line_score(line: int, open_ends: int) -> int:
        if line >= 5:
            return 100000
        if line == 4 and open_ends == 2:
            return 15000
        if line == 4 and open_ends == 1:
            return 5000
        if line == 3 and open_ends == 2:
            return 1200
        if line == 3 and open_ends == 1:
            return 300
        if line == 2 and open_ends == 2:
            return 120
        if line == 2 and open_ends == 1:
            return 40
        return 8 + open_ends

    @staticmethod
    def _count(board: list[list[int]], row: int, col: int, dr: int, dc: int, color: int) -> int:
        total = 0
        r = row + dr
        c = col + dc
        while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and board[r][c] == color:
            total += 1
            r += dr
            c += dc
        return total

    @staticmethod
    def _is_open(board: list[list[int]], row: int, col: int) -> bool:
        return 0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE and board[row][col] == EMPTY
