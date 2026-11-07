1. The most useful things from the course

I liked learning how a compiler works, one step at a time. In the lexer practice, we worked on splitting a program into smaller parts. In the parser practice, we used those parts to understand the program. These examples helped me see how the compiler reads code. Before the course, I thought a compiler was just one big program.


2. What the course could explain better

The lectures showed examples from different programming languages, but in the practicals we mostly worked on our own compiler. We did not write the same small program in the other languages. Because of this, it was hard for me to see how the differences work in real code. A few short examples from those languages would help.


3. Things that surprised me

- I thought that if a line of code was written correctly, the compiler would accept it. But in the type-checking practice, we saw that a line can be written correctly and still use the wrong types. For example, a Boolean value cannot be added to a number.
- I was surprised that lambdas can work differently in different languages. We talked about this when learning how languages use functions and values from the surrounding code.
- I thought the compiler could remove any code that was not used. But the tests/opt/kept_call_prints.txt test shows that a call which prints text must stay. Otherwise, the program would do something different.


4. What I still do not understand

I cannot name one big topic that I did not understand. Overall, I understand the path from writing a program to compiling it. I do still have questions about some details. For example, I would like to understand better why the compiler allows comparing i32 and i64, but does not allow comparing a number and a Boolean value.


5. Other ideas for the course

I would add a short discussion of a common mistake at the end of some practicals. For example, we could look at a program that is written correctly but fails the type check, and talk about why. This would help me understand what each compiler error means.