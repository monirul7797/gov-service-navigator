@echo off

echo ========================================
echo Running test_multiturn.py
echo ========================================
python test_multiturn.py

echo.
echo ========================================
echo Running eval_nid.py
echo ========================================
python eval_nid.py

echo.
echo ========================================
echo Running eval_epassport.py
echo ========================================
python eval_epassport.py

echo.
echo ========================================
echo Running eval_bdris.py
echo ========================================
python eval_bdris.py

echo.
echo ========================================
echo Running eval_ereturn.py
echo ========================================
python eval_ereturn.py

echo.
echo ========================================
echo ALL TESTS FINISHED
echo ========================================
pause