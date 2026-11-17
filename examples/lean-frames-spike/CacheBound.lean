/-
  The frame machine's property cache-bound, `[true* . o_cache] false`, proved
  on the refined Core that naima-codegen generates, for every configuration
  (w, r, cache, the slow-start limit), every program the inputs give and every
  interpretation of the maps that satisfies the model's equations.

  The invariant: the index ix_kept lists, without repetition, exactly the
  evaluations marked cached, and kept is its length. Then kept > cache makes
  ix_kept non-empty with a cached head, so the guard of evict (ix_en_25)
  holds, and the refined Core is not quiescent: o_cache never fires.
-/
import Frames

open Mcrl2

namespace Frames

noncomputable section

-- For `List.erase` in the proof only; the model's equality stays `Mcrl2.eqb`.
deriving instance DecidableEq for Eval

variable {D : Data}

/-- The cache invariant, on the three parameters it is about. -/
def CInv (c : Eval → Bool) (k : Nat) (L : List Eval) : Prop :=
  (∀ e, e ∈ L ↔ c e = true) ∧ L.Nodup ∧ k = L.length

def Inv (s : State) : Prop := CInv s.cached s.kept s.ix_kept

/-- The upkeep of ix_kept that every summand writing cached appends (refine.py). -/
def upk (D : Data) (c : Eval → Bool) (L : List Eval) (x : Eval) (b : Bool) : List Eval :=
  let ix_a1 : Bool := D.ix_mem_kept c x
  let ix_b1 : Bool := D.ix_mem_kept (upd c x b) x
  D.ix_ins_kept (D.ix_out_kept L (cnd (ix_a1 && !ix_b1) (fins x fempty) fempty))
    (cnd (ix_b1 && !ix_a1) (fins x fempty) fempty) 0

theorem out_kept (hD : Laws D) (s : FSet Eval) :
    ∀ L : List Eval, D.ix_out_kept L s = L.filter (fun y => !s y)
  | [] => hD.ix_out_kept_eq1 s
  | y :: l => by
    rw [hD.ix_out_kept_eq2, out_kept hD s l]
    cases h : s y <;> simp [h, cnd, List.filter]

theorem out_kept_empty (hD : Laws D) (L : List Eval) : D.ix_out_kept L fempty = L := by
  rw [out_kept hD]; simp [fempty]

theorem ins_kept_empty (hD : Laws D) (L : List Eval) (i : Nat) : D.ix_ins_kept L fempty i = L :=
  hD.ix_ins_kept_eq1 L fempty i (by simp)

theorem fins_ne (x : Eval) : (eqb (fins x fempty) (fempty : FSet Eval)) = false := by
  simp only [eqb_eq_false]
  intro h
  have := congrFun h x
  simp [fins, fempty] at this

theorem ins_kept_single (hD : Laws D) (L : List Eval) (j : Nat) :
    ∀ d i, i + d = j → D.ix_ins_kept L (fins (Eval.ev j) fempty) i = L ++ [Eval.ev j]
  | 0, i, h => by
    simp only [Nat.add_zero] at h; subst h
    rw [hD.ix_ins_kept_eq2 L _ i (by simp [fins_ne, fins])]
    have : fdiff (fins (Eval.ev i) fempty) (fins (Eval.ev i) fempty) = (fempty : FSet Eval) := by
      funext y; simp [fdiff, fempty]
    rw [this, ins_kept_empty hD]
  | d + 1, i, h => by
    have hne : (Eval.ev i) ≠ (Eval.ev j) := by intro he; cases he; omega
    rw [hD.ix_ins_kept_eq3 L _ i (by simp [fins_ne, fins, fempty, hne])]
    exact ins_kept_single hD L j d (i + 1) (by omega)

theorem ins_kept_one (hD : Laws D) (L : List Eval) (x : Eval) :
    D.ix_ins_kept L (fins x fempty) 0 = L ++ [x] := by
  cases x with
  | ev j => exact ins_kept_single hD L j j 0 (by omega)

theorem upd_self (c : Eval → Bool) (x : Eval) : upd c x (c x) = c := by
  funext y; by_cases h : y = x
  · subst h; simp
  · exact upd_other c x y _ h

theorem int2nat_pred (hD : Laws D) (k : Nat) (hk : 0 < k) :
    D.Int2Nat (Int.ofNat k - Int.ofNat 1) = k - 1 := by
  have : Int.ofNat k - Int.ofNat 1 = Int.ofNat (k - 1) := by simp only [Int.ofNat_eq_natCast]; omega
  rw [this, hD.Int2Nat_ofNat]

/-- Dropping x from the cache. -/
theorem cinv_drop (hD : Laws D) {c k L} (hs : CInv c k L) (x : Eval) (hx : c x = true) :
    CInv (upd c x false) (D.Int2Nat (Int.ofNat k - Int.ofNat 1)) (upk D c L x false) := by
  obtain ⟨hm, hn, hk⟩ := hs
  have hxL : x ∈ L := (hm x).2 hx
  have hpos : 0 < k := by rw [hk]; exact List.length_pos_of_mem hxL
  simp only [upk, hD.ix_mem_kept_eq1, hx, upd_same, Bool.not_false, Bool.and_true, Bool.not_true,
    Bool.and_false, cnd_true, cnd_false, ins_kept_empty hD, out_kept hD, int2nat_pred hD k hpos]
  refine ⟨?_, hn.filter _, ?_⟩
  · intro e
    by_cases he : e = x
    · subst he; simp [fins, fempty]
    · simp [fins, fempty, he, upd_other c x e false he, hm e]
  · rw [hk]
    have hfx : L.filter (fun y => !fins x fempty y) = L.erase x := by
      rw [List.Nodup.erase_eq_filter hn]; congr; funext y
      by_cases h : y = x
      · simp [h, fins, fempty]
      · have h1 : (y != x) = true := bne_iff_ne.2 h
        simp [h1, fins, fempty, eqb, h]
    rw [hfx, List.length_erase_of_mem hxL]

/-- Adding x to the cache. -/
theorem cinv_keep (hD : Laws D) {c k L} (hs : CInv c k L) (x : Eval) (hx : c x = false) :
    CInv (upd c x true) (k + 1) (upk D c L x true) := by
  obtain ⟨hm, hn, hk⟩ := hs
  have hxL : x ∉ L := fun h => by rw [(hm x).1 h] at hx; cases hx
  simp only [upk, hD.ix_mem_kept_eq1, hx, upd_same, Bool.not_false, Bool.and_true, Bool.not_true,
    Bool.and_false, cnd_true, cnd_false, out_kept_empty hD, ins_kept_one hD]
  refine ⟨?_, ?_, by simp [hk]⟩
  · intro e
    by_cases he : e = x
    · subst he; simp
    · simp [he, upd_other c x e true he, hm e]
  · rw [List.nodup_append]
    refine ⟨hn, by simp, ?_⟩
    intro a ha b hb
    simp at hb
    subst hb
    intro h; subst h; exact hxL ha

/-- Taking x out of the cache when it is there: the summands that revive or
share an evaluation the cache may hold. -/
theorem cinv_revive (hD : Laws D) {c k L} (hs : CInv c k L) (x : Eval) :
    CInv (upd c x false) (cnd (c x) (D.Int2Nat (Int.ofNat k - Int.ofNat 1)) k) (upk D c L x false) := by
  cases hx : c x with
  | true => exact cinv_drop hD hs x hx
  | false =>
    have hc : upd c x false = c := by rw [← hx]; exact upd_self c x
    simp only [cnd_false]
    have : upk D c L x false = L := by
      simp only [upk, hD.ix_mem_kept_eq1, hx, hc, Bool.false_and, cnd_false, out_kept_empty hD,
        ins_kept_empty hD]
    rw [hc, this]; exact hs

theorem cinv_initial (w r cache limit : Nat) : Inv (initial w r cache limit) := by
  refine ⟨?_, List.nodup_nil, rfl⟩
  intro e; simp [initial]

theorem cached_of_en25 (hD : Laws D) {cache c k L}
    (h : D.ix_en_25 cache c k L = true) : c (D.ix_first_kept L) = true := by
  rw [hD.ix_en_25_eq1] at h
  simp only [Bool.and_eq_true] at h
  exact h.2.1

theorem en25_of_over (hD : Laws D) {cache c k L} (hs : CInv c k L) (hover : k > cache) :
    D.ix_en_25 cache c k L = true := by
  obtain ⟨hm, _, hk⟩ := hs
  cases L with
  | nil => simp at hk; omega
  | cons y l =>
    rw [hD.ix_en_25_eq1, hD.ix_some_kept_eq2, hD.ix_first_kept_eq2]
    simp [cnd, (hm y).1 (List.mem_cons_self ..), hover]

theorem step_inv (hD : Laws D) {s a t} (h : Step D s a t) (hs : Inv s) : Inv t := by
  cases h
  all_goals first
    | exact hs
    | skip
  case s44 hc =>
    simp only [Bool.and_eq_true] at hc
    exact cinv_drop hD hs _ (cached_of_en25 hD hc.1)
  case s47 hc =>
    refine cinv_keep hD hs _ ?_
    simp only [Bool.and_eq_true] at hc
    have h28 := hc.1
    cases hx : s.cached (D.ix_first_keepable s.phase s.missing s.holders s.waiters s.again
        s.cached s.ne 0) with
    | false => rfl
    | true =>
      rw [hD.ix_en_28_eq1] at h28
      simp [hx] at h28
  all_goals exact cinv_revive hD hs _

theorem reach_inv (hD : Laws D) {i s : State} (hi : Inv i) (h : Reach (Step D) i s) : Inv s := by
  induction h with
  | init => exact hi
  | step _ hst ih => exact step_inv hD hst ih

theorem cache_bound : prop_cache_bound := by
  intro D hD w r cache limit s hr t ht
  have hs := reach_inv hD (cinv_initial w r cache limit) hr
  cases ht with
  | s72 hc =>
    simp only [Bool.and_eq_true] at hc
    obtain ⟨hq, hover⟩ := hc
    have h25 := en25_of_over hD hs (by simpa using hover)
    rw [hD.ix_quiet_eq1] at hq
    simp [h25] at hq

end

end Frames

#print axioms Frames.cache_bound
