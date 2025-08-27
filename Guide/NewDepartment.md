# Make a New Department for HAO!

Bored with the existing departments? Want to add a new one? You've come to the right place!

This guide will walk you through the information you need to design a new department. No modding skills are required! Just send the information to me, and I'll help bring it to life.

Before you start, you can take a look at the [information HAO already includes](./UsedEntities.json). If you find anything that your new department can use, feel free to use it directly!

In Project Hospital, all the diseases associated with a department are primarily described by the following four pieces of information:

1.  **Diagnose**: A disease **must have one and only one** main symptom and multiple secondary symptoms. The main symptom for each disease must be **unique**. The examination and treatment methods for a disease are entirely determined by the examination and treatment methods of its symptoms. If the main symptom of the disease is cured, the disease is considered cured.
    1.  **Required Information**: Disease name, one associated main symptom, and multiple secondary symptoms.
    2.  **Optional Information**: Frequency of occurrence, cost of treatment.

2.  **Symptom**: A symptom can be identified by multiple examination methods and treated by one specific treatment method.
    1.  **Required Information**: Symptom name, at least one examination method, a unique treatment method (including surgery), and whether it is a main symptom.
    2.  **Optional Information**: Discomfort level, whether the patient will complain about it, danger level, whether the patient can move, shyness level, possible complications.

3.  **Examination**: A method used to detect a symptom.
    1.  **Required Information**: Examination name, required room (e.g., Doctor's Office, Lab, Radiology).
    2.  **Optional Information**: Duration of examination, discomfort level.

4.  **Treatment**: A method (including surgery) to treat a symptom.
    1.  **Required Information**: Treatment name, treatment type (non-surgical or surgical), whether hospitalization is required.
    2.  **Optional Information**: Discomfort level.

After designing the information above, you can send it to me via Google Drive or any other convenient method. I will use this information to create the new department as soon as possible!